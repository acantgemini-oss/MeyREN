import asyncio
import time
import threading
from typing import Dict, Set

import db

# ══════════════════════════════════════════════════════════════════════════════
# Token-Bucket Bandwidth Limiter (Mbps -> bytes/sec)
# ══════════════════════════════════════════════════════════════════════════════

MIN_RATE_BYTES = 16 * 1024       # 16 KB/s minimum token rate
MIN_BURST_BYTES = 32 * 1024      # 32 KB minimum capacity

class _TokenBucket:
    __slots__ = ("rate", "capacity", "tokens", "last_refill")

    def __init__(self, rate_bytes_per_sec: float):
        self.rate = max(float(rate_bytes_per_sec), MIN_RATE_BYTES)
        # Capacity allows up to 1 second of burst, with a minimum floor
        self.capacity = max(self.rate, MIN_BURST_BYTES)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.last_refill = now
            self.tokens = min(self.capacity, self.tokens + (elapsed * self.rate))

    async def consume(self, nbytes: int):
        if nbytes <= 0:
            return
        while True:
            self._refill()
            if self.tokens >= nbytes:
                self.tokens -= nbytes
                return
            deficit = nbytes - self.tokens
            wait_sec = deficit / self.rate
            # Sleep in bounded chunks to remain responsive to cancellations
            await asyncio.sleep(min(max(wait_sec, 0.005), 0.25))


_BUCKETS: Dict[str, _TokenBucket] = {}
_BUCKETS_LOCK = threading.Lock()


def _get_or_create_bucket(uuid: str, rate_bytes_per_sec: float) -> _TokenBucket:
    with _BUCKETS_LOCK:
        b = _BUCKETS.get(uuid)
        target_rate = max(rate_bytes_per_sec, MIN_RATE_BYTES)
        if b is None or abs(b.rate - target_rate) > 1024:
            b = _TokenBucket(target_rate)
            _BUCKETS[uuid] = b
        return b


async def throttle(uuid: str, nbytes: int):
    """Throttles async data stream based on uuid's configured speed_limit_mbps."""
    if nbytes <= 0:
        return
    link = db.get_link(uuid)
    if not link:
        return
    mbps = float(link.get("speed_limit_mbps", 0.0) or 0.0)
    if mbps <= 0.0:
        return  # Unlimited speed
    # Convert Mbps (Megabits per sec) to bytes per sec
    rate_bytes_sec = (mbps * 1_000_000.0) / 8.0
    bucket = _get_or_create_bucket(uuid, rate_bytes_sec)
    await bucket.consume(nbytes)


def reset_throttle(uuid: str):
    with _BUCKETS_LOCK:
        _BUCKETS.pop(uuid, None)


# ══════════════════════════════════════════════════════════════════════════════
# Concurrent IP Limit & Connection Gatekeeper
# ══════════════════════════════════════════════════════════════════════════════

_CONNECTIONS: Dict[str, dict] = {}
_CONNS_LOCK = threading.Lock()


def get_active_ips_for_uuid(uuid: str) -> Set[str]:
    with _CONNS_LOCK:
        return {
            c["ip"] for c in _CONNECTIONS.values()
            if c.get("uuid") == uuid and c.get("ip") and c["ip"] != "unknown"
        }


def get_active_connections_count() -> int:
    with _CONNS_LOCK:
        return len(_CONNECTIONS)


def get_connections_summary() -> list:
    with _CONNS_LOCK:
        return [dict(c) for c in _CONNECTIONS.values()]


def is_ip_allowed(uuid: str, client_ip: str) -> bool:
    """Checks if client_ip is permitted under the link's max_ips concurrent threshold."""
    if not client_ip or client_ip == "unknown":
        return True

    link = db.get_link(uuid)
    if not link:
        return False

    max_ips = int(link.get("max_ips", 0) or 0)
    if max_ips <= 0:
        return True  # 0 means unlimited concurrent IPs

    active_ips = get_active_ips_for_uuid(uuid)
    if client_ip in active_ips:
        return True  # Already connected from this IP

    return len(active_ips) < max_ips


def register_connection(conn_id: str, uuid: str, client_ip: str, transport: str = "ws"):
    with _CONNS_LOCK:
        _CONNECTIONS[conn_id] = {
            "id": conn_id,
            "uuid": uuid,
            "ip": client_ip,
            "transport": transport,
            "connected_at": time.time(),
        }
    db.touch_last_connected(uuid)


def unregister_connection(conn_id: str):
    with _CONNS_LOCK:
        _CONNECTIONS.pop(conn_id, None)


async def prune_stale_connections_loop():
    """Background task to ensure connections older than 24h with no traffic get pruned."""
    while True:
        await asyncio.sleep(120)
        now = time.time()
        with _CONNS_LOCK:
            stale = [cid for cid, c in _CONNECTIONS.items() if now - c.get("connected_at", now) > 86400 * 2]
            for cid in stale:
                _CONNECTIONS.pop(cid, None)

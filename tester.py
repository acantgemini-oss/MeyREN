import asyncio
import time
from typing import Dict, List, Optional
import httpx

from utils import CF_CLEAN_IPS


async def test_tcp_latency(host: str, port: int = 443, timeout: float = 3.0) -> Dict:
    """Measures raw TCP handshake latency in milliseconds."""
    t0 = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        latency_ms = (time.monotonic() - t0) * 1000.0
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return {
            "host": host,
            "port": port,
            "success": True,
            "latency_ms": round(latency_ms, 1),
            "status": "online",
        }
    except asyncio.TimeoutError:
        return {
            "host": host,
            "port": port,
            "success": False,
            "latency_ms": -1,
            "status": "timeout",
            "error": "Connection timed out",
        }
    except Exception as e:
        return {
            "host": host,
            "port": port,
            "success": False,
            "latency_ms": -1,
            "status": "error",
            "error": str(e),
        }


async def test_http_latency(url: str, timeout: float = 4.0) -> Dict:
    """Measures full HTTP response latency."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            resp = await client.get(url)
            latency_ms = (time.monotonic() - t0) * 1000.0
            return {
                "url": url,
                "status_code": resp.status_code,
                "latency_ms": round(latency_ms, 1),
                "success": resp.status_code < 500,
            }
    except Exception as e:
        return {
            "url": url,
            "success": False,
            "latency_ms": -1,
            "error": str(e),
        }


async def benchmark_clean_ips(candidates: Optional[List[str]] = None) -> List[Dict]:
    """Tests all clean IP candidates concurrently and returns them sorted by latency."""
    ips = candidates or CF_CLEAN_IPS
    tasks = [test_tcp_latency(ip, 443, timeout=2.5) for ip in ips]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    # Sort successful ones first by lowest latency
    results.sort(key=lambda x: (not x["success"], x["latency_ms"]))
    return results

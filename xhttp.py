import asyncio
import secrets
import socket
import time
from typing import Dict, Optional

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import StreamingResponse

import db
import limiter
from utils import parse_vless_header, get_client_ip

router = APIRouter()

XHTTP_BUF = 256 * 1024
DOWNLINK_QUEUE_MAX = 512
SESSION_IDLE_TIMEOUT = 45.0
TCP_CONNECT_TIMEOUT = 10.0
SOCK_BUF_SIZE = 1 * 1024 * 1024

_SESSIONS: Dict[str, dict] = {}
_SESSIONS_LOCK = asyncio.Lock()


def _tune_socket(writer: asyncio.StreamWriter):
    sock = writer.transport.get_extra_info("socket")
    if not sock:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCK_BUF_SIZE)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCK_BUF_SIZE)
    except OSError:
        pass


async def _open_tcp_from_header(first_chunk: bytes):
    command, address, port, payload = await parse_vless_header(first_chunk)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(address, port), timeout=TCP_CONNECT_TIMEOUT
    )
    _tune_socket(writer)
    if payload:
        writer.write(payload)
        await writer.drain()
    return reader, writer, address, port


async def _teardown_session(session_id: str):
    async with _SESSIONS_LOCK:
        sess = _SESSIONS.pop(session_id, None)
    if not sess:
        return
    sess["closed"] = True
    conn_id = sess.get("conn_id")
    if conn_id:
        limiter.unregister_connection(conn_id)

    writer = sess.get("writer")
    if writer:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    dq = sess.get("down_q")
    if dq:
        try:
            dq.put_nowait(None)
        except Exception:
            pass


async def _pump_tcp_to_queue(session_id: str, uuid: str, reader: asyncio.StreamReader, down_q: asyncio.Queue):
    """Pumps bytes from remote TCP socket into downlink HTTP streaming queue."""
    try:
        while True:
            chunk = await reader.read(XHTTP_BUF)
            if not chunk:
                break
            nbytes = len(chunk)
            # 1. Quota check
            if not db.check_quota_fast(uuid, nbytes):
                break
            # 2. Speed limiting throttle
            await limiter.throttle(uuid, nbytes)
            # 3. Buffer usage
            db.add_usage_buffered(uuid, nbytes)
            # 4. Push to downlink queue
            await down_q.put(chunk)
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        await down_q.put(None)
        await _teardown_session(session_id)


@router.post("/xhttp/{uuid}")
async def xhttp_handle_stream(uuid: str, request: Request):
    """Handles VLESS XHTTP streaming requests over HTTP/2 or HTTP/3."""
    # 1. Check link authorization
    if not db.check_quota_fast(uuid):
        raise HTTPException(status_code=403, detail="Quota exceeded or link inactive")

    client_ip = get_client_ip(request)
    if not limiter.is_ip_allowed(uuid, client_ip):
        raise HTTPException(status_code=403, detail="Concurrent IP limit reached")

    session_id = request.headers.get("x-session-id") or secrets.token_urlsafe(16)
    conn_id = f"xhttp_{session_id[:8]}"
    limiter.register_connection(conn_id, uuid, client_ip, transport="xhttp")

    down_q = asyncio.Queue(maxsize=DOWNLINK_QUEUE_MAX)
    sess = {
        "session_id": session_id,
        "uuid": uuid,
        "conn_id": conn_id,
        "down_q": down_q,
        "writer": None,
        "reader": None,
        "tcp_open": False,
        "last_seen": time.time(),
        "closed": False,
    }

    async with _SESSIONS_LOCK:
        _SESSIONS[session_id] = sess

    # Read the initial chunk from the request body
    body_stream = request.stream()
    first_chunk = b""
    try:
        async for chunk in body_stream:
            first_chunk += chunk
            if len(first_chunk) >= 24:
                break
    except Exception:
        await _teardown_session(session_id)
        raise HTTPException(status_code=400, detail="Failed reading header stream")

    if len(first_chunk) < 24:
        await _teardown_session(session_id)
        raise HTTPException(status_code=400, detail="Stream chunk too small for VLESS")

    # Connect to target destination
    try:
        reader, writer, target_host, target_port = await _open_tcp_from_header(first_chunk)
        sess["reader"] = reader
        sess["writer"] = writer
        sess["tcp_open"] = True
    except Exception as e:
        await _teardown_session(session_id)
        raise HTTPException(status_code=502, detail=f"Target connection failed: {str(e)}")

    # Start background reader task from TCP to queue
    asyncio.create_task(_pump_tcp_to_queue(session_id, uuid, reader, down_q))

    # Background task to pump remainder of request body to TCP writer
    async def _pump_uplink():
        try:
            async for chunk in body_stream:
                if sess.get("closed"):
                    break
                nbytes = len(chunk)
                if not db.check_quota_fast(uuid, nbytes):
                    break
                await limiter.throttle(uuid, nbytes)
                db.add_usage_buffered(uuid, nbytes)
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass

    asyncio.create_task(_pump_uplink())

    # Stream downlink queue back as HTTP response
    async def _downlink_generator():
        try:
            # Send VLESS success response header (0x00, 0x00)
            yield b"\x00\x00"
            while True:
                chunk = await down_q.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            await _teardown_session(session_id)

    resp_headers = {
        "content-type": "application/grpc",
        "cache-control": "no-cache, no-store, must-revalidate",
        "x-accel-buffering": "no",
        "x-session-id": session_id,
    }
    return StreamingResponse(_downlink_generator(), headers=resp_headers)


@router.post("/xhttp/{uuid}/{session_id}")
async def xhttp_handle_packet(uuid: str, session_id: str, request: Request):
    """Handles supplemental packet-up chunks for an active session."""
    async with _SESSIONS_LOCK:
        sess = _SESSIONS.get(session_id)

    if not sess or sess.get("closed") or not sess.get("writer"):
        raise HTTPException(status_code=404, detail="Session expired or not found")

    sess["last_seen"] = time.time()
    body = await request.body()
    if body:
        nbytes = len(body)
        if not db.check_quota_fast(uuid, nbytes):
            raise HTTPException(status_code=403, detail="Quota exhausted")
        await limiter.throttle(uuid, nbytes)
        db.add_usage_buffered(uuid, nbytes)
        writer = sess["writer"]
        writer.write(body)
        await writer.drain()

    return Response(status_code=204)

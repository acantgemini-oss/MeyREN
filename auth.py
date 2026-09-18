import asyncio
import time
import secrets
from fastapi import Request, HTTPException, Depends

SESSION_COOKIE = "meyren_session"
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days

SESSIONS: dict = {}
SESSIONS_LOCK = asyncio.Lock()


def _prune_expired_locked(now: float):
    expired = [t for t, data in SESSIONS.items() if (isinstance(data, dict) and data.get("exp", 0) < now) or (isinstance(data, (int, float)) and data < now)]
    for t in expired:
        SESSIONS.pop(t, None)


async def create_session(username: str = "admin", role: str = "superadmin", permissions: list = None) -> str:
    if permissions is None:
        permissions = ["all"] if role == "superadmin" else ["manage_users", "view_stats"]
    token = secrets.token_urlsafe(32)
    now = time.time()
    async with SESSIONS_LOCK:
        _prune_expired_locked(now)
        SESSIONS[token] = {
            "username": username,
            "role": role,
            "permissions": permissions,
            "exp": now + SESSION_TTL,
            "created_at": now,
        }
    return token


async def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    now = time.time()
    async with SESSIONS_LOCK:
        sess = SESSIONS.get(token)
        if sess is None:
            return None
        # Handle legacy session formats
        if isinstance(sess, (int, float)):
            if sess < now:
                SESSIONS.pop(token, None)
                return None
            return {"username": "admin", "role": "superadmin", "permissions": ["all"], "exp": sess}
        if sess.get("exp", 0) < now:
            SESSIONS.pop(token, None)
            return None
        return dict(sess)


async def is_valid_session(token: str | None) -> bool:
    sess = await get_session(token)
    return sess is not None


async def destroy_session(token: str | None):
    if token:
        async with SESSIONS_LOCK:
            SESSIONS.pop(token, None)


async def clear_other_sessions(current_token: str | None):
    async with SESSIONS_LOCK:
        saved_sess = SESSIONS.get(current_token) if current_token else None
        SESSIONS.clear()
        if current_token and saved_sess:
            if isinstance(saved_sess, dict):
                saved_sess["exp"] = time.time() + SESSION_TTL
                SESSIONS[current_token] = saved_sess
            else:
                SESSIONS[current_token] = time.time() + SESSION_TTL


async def cleanup_expired_sessions():
    now = time.time()
    async with SESSIONS_LOCK:
        _prune_expired_locked(now)


async def require_auth(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    sess = await get_session(token)
    if not sess:
        raise HTTPException(status_code=401, detail="unauthorized")
    return sess


def require_permission(required_permission: str):
    """FastAPI dependency to enforce server-side permission checks."""
    async def _perm_checker(request: Request, sess: dict = Depends(require_auth)) -> dict:
        role = sess.get("role", "")
        perms = sess.get("permissions", [])
        if role == "superadmin" or "all" in perms or required_permission in perms:
            return sess
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: Requires '{required_permission}' privilege"
        )
    return _perm_checker

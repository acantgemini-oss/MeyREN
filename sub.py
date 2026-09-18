import base64
import json
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

import db
import protocols
from utils import get_domain, format_bytes, format_expiration

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _build_userinfo_header(link: dict) -> str:
    """Builds standard SIP008 / Clash / v2ray subscription-userinfo header."""
    used = link.get("used_bytes", 0)
    limit = link.get("limit_bytes", 0)
    expire_ts = 0
    expires_at = link.get("expires_at")
    if expires_at:
        try:
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            expire_ts = int(dt.timestamp())
        except Exception:
            pass
    return f"upload=0; download={used}; total={limit}; expire={expire_ts}"


@router.get("/sub/{token}")
async def get_subscription(token: str, request: Request, format: Optional[str] = None):
    """Universal subscription endpoint compatible with v2rayNG, Sing-box, Clash, and Shadowrocket."""
    link = db.get_link_by_sub_token(token)
    if not link:
        # Fallback: check if the token is a raw uuid
        link = db.get_link(token)
        if not link:
            raise HTTPException(status_code=404, detail="Subscription token not found")

    domain = get_domain()
    user_agent = request.headers.get("user-agent", "").lower()
    headers = {
        "subscription-userinfo": _build_userinfo_header(link),
        "profile-title": f"MeyREN - {link.get('label', 'Node')}",
        "profile-update-interval": "6",
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-cache, no-store, must-revalidate",
    }

    # If client is expired or disabled, return zero configs but keep userinfo header so app displays status
    if not link.get("active", 1):
        return Response(content=base64.b64encode(b"").decode("utf-8"), headers=headers)

    # Determine requested format
    fmt = (format or "").lower().strip()
    if not fmt:
        if "sing-box" in user_agent or "singbox" in user_agent:
            fmt = "singbox"
        elif "clash" in user_agent or "meta" in user_agent:
            fmt = "clash"
        else:
            fmt = "base64"

    if fmt == "singbox":
        profile_json = protocols.generate_singbox_profile(link, domain)
        headers["content-type"] = "application/json; charset=utf-8"
        return Response(content=json.dumps(profile_json, indent=2), headers=headers)

    if fmt == "clash":
        profile_yaml = protocols.generate_clash_profile(link, domain)
        headers["content-type"] = "text/yaml; charset=utf-8"
        return Response(content=profile_yaml, headers=headers)

    # Default Base64 encoded list of URIs
    config_list = protocols.generate_all_protocols_for_link(link, domain)
    raw_lines = "\n".join(cfg["uri"] for cfg in config_list)
    encoded = base64.b64encode(raw_lines.encode("utf-8")).decode("utf-8")
    return Response(content=encoded, headers=headers)


@router.get("/sub/mix/{group_id}")
async def get_group_subscription(group_id: int, request: Request):
    """Combines all active configurations in a group into a single aggregate subscription."""
    domain = get_domain()
    links = [l for l in db.get_links() if l.get("group_id") == group_id and l.get("active", 1)]
    if not links:
        raise HTTPException(status_code=404, detail="No active links found in this group")

    all_uris = []
    for link in links:
        cfgs = protocols.generate_all_protocols_for_link(link, domain)
        for c in cfgs:
            all_uris.append(c["uri"])

    raw_lines = "\n".join(all_uris)
    encoded = base64.b64encode(raw_lines.encode("utf-8")).decode("utf-8")
    headers = {
        "profile-title": f"MeyREN - Group {group_id}",
        "profile-update-interval": "6",
        "content-type": "text/plain; charset=utf-8",
    }
    return Response(content=encoded, headers=headers)


@router.get("/client/{token}", response_class=HTMLResponse)
async def client_portal_page(token: str, request: Request):
    """Public customer portal providing 1-click import, QR codes, and live usage stats."""
    link = db.get_link_by_sub_token(token)
    if not link:
        link = db.get_link(token)
        if not link:
            raise HTTPException(status_code=404, detail="Client portal link not found")

    domain = get_domain()
    used_bytes = link.get("used_bytes", 0)
    limit_bytes = link.get("limit_bytes", 0)
    used_str = format_bytes(used_bytes)
    limit_str = "Unlimited" if limit_bytes == 0 else format_bytes(limit_bytes)
    
    pct = 0
    if limit_bytes > 0:
        pct = min(100, int((used_bytes / limit_bytes) * 100))

    rem_str = "Unlimited"
    if limit_bytes > 0:
        rem_str = format_bytes(max(0, limit_bytes - used_bytes))

    exp_str, is_expired = format_expiration(link.get("expires_at"))
    
    configs = protocols.generate_all_protocols_for_link(link, domain)
    sub_url = f"{request.url.scheme}://{request.url.netloc}/sub/{link.get('sub_token') or link['uuid']}"
    announcements = db.get_announcements(active_only=True)

    return templates.TemplateResponse(
        "client_portal.html",
        {
            "request": request,
            "link": link,
            "used_str": used_str,
            "limit_str": limit_str,
            "rem_str": rem_str,
            "pct": pct,
            "exp_str": exp_str,
            "is_expired": is_expired,
            "configs": configs,
            "sub_url": sub_url,
            "domain": domain,
            "announcements": announcements,
        },
    )

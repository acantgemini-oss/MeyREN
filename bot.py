import asyncio
import os
import time
import logging
from typing import Set, Optional
from datetime import datetime, timedelta

import httpx

import db
import utils
import protocols

logger = logging.getLogger("meyren.bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_admin_ids_str = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
ADMIN_IDS: Set[int] = {int(x) for x in _admin_ids_str.replace(" ", "").split(",") if x.isdigit()} if _admin_ids_str else set()
FORCE_CHANNEL = os.environ.get("TELEGRAM_FORCE_JOIN_CHANNEL", "").strip()

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
_running = False
_poll_task: Optional[asyncio.Task] = None


async def send_message(chat_id: int, text: str, reply_markup: dict = None, parse_mode: str = "Markdown") -> bool:
    if not API_URL:
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{API_URL}/sendMessage", json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram sendMessage failed: {e}")
        return False


async def check_channel_membership(user_id: int) -> bool:
    if not FORCE_CHANNEL or not API_URL:
        return True
    try:
        channel = FORCE_CHANNEL if FORCE_CHANNEL.startswith("@") else f"@{FORCE_CHANNEL}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{API_URL}/getChatMember", params={"chat_id": channel, "user_id": user_id})
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("result", {}).get("status", "")
                return status in ("creator", "administrator", "member")
    except Exception:
        pass
    return False


async def handle_update(update: dict, domain: str, start_time: float):
    msg = update.get("message") or update.get("edited_message")
    if not msg or "text" not in msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg["text"].strip()
    is_admin = (not ADMIN_IDS) or (user_id in ADMIN_IDS)

    # Enforce channel join if configured and not admin
    if not is_admin and FORCE_CHANNEL:
        joined = await check_channel_membership(user_id)
        if not joined:
            channel_link = f"https://t.me/{FORCE_CHANNEL.replace('@', '')}"
            kb = {"inline_keyboard": [[{"text": "📢 Join Channel", "url": channel_link}]]}
            await send_message(
                chat_id,
                f"⚠️ *Access Restricted*\n\nPlease join our channel first to use this bot:\n{channel_link}",
                reply_markup=kb,
            )
            return

    # Command dispatcher
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd == "/start":
        greeting = (
            "⚡ *Welcome to MeyREN Gateway Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "This bot provides direct status lookup and node management.\n\n"
            "📌 *User Commands:*\n"
            "• `/status <uuid>` — Check your node usage and quota\n\n"
        )
        if is_admin:
            greeting += (
                "👑 *Admin Commands:*\n"
                "• `/system` — View live server health & traffic stats\n"
                "• `/users` — List recent client links\n"
                "• `/new <name> <gb> [days]` — Instantly create a new user\n"
                "• `/del <uuid>` — Delete a client node\n"
            )
        await send_message(chat_id, greeting)

    elif cmd in ("/system", "/stats"):
        if not is_admin:
            await send_message(chat_id, "⛔ Access denied.")
            return

        links = db.get_links()
        total_users = len(links)
        active_users = sum(1 for l in links if l.get("active", 1))
        total_used = sum(l.get("used_bytes", 0) for l in links)
        up = utils.uptime(start_time)

        msg_text = (
            "📊 *MeyREN System Health*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ *Uptime:* `{up}`\n"
            f"👥 *Total Users:* `{total_users}` (Active: `{active_users}`)\n"
            f"📈 *Total Traffic:* `{utils.format_bytes(total_used)}`\n"
            f"🌐 *Domain:* `{domain}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await send_message(chat_id, msg_text)

    elif cmd in ("/user", "/status"):
        if len(parts) < 2:
            await send_message(chat_id, "Usage: `/status <uuid>`")
            return
        target = parts[1].strip()
        link = db.get_link(target) or db.get_link_by_sub_token(target)
        if not link:
            # Search by label if admin
            if is_admin:
                matches = [l for l in db.get_links() if l.get("label", "").lower() == target.lower()]
                if matches:
                    link = matches[0]

        if not link:
            await send_message(chat_id, "❌ User not found.")
            return

        reply = utils.format_bot_reply(
            link.get("label", "Node"),
            link.get("used_bytes", 0),
            link.get("limit_bytes", 0),
            bool(link.get("active", 1)),
            link.get("expires_at"),
            link.get("speed_limit_mbps", 0.0),
        )
        # Add subscription link button
        sub_url = f"https://{domain}/sub/{link.get('sub_token') or link['uuid']}"
        kb = {"inline_keyboard": [[{"text": "📋 Copy Subscription URL", "url": sub_url}]]}
        await send_message(chat_id, reply, reply_markup=kb)

    elif cmd == "/users":
        if not is_admin:
            await send_message(chat_id, "⛔ Access denied.")
            return
        links = db.get_links()[:10]  # Show top 10
        if not links:
            await send_message(chat_id, "No users registered yet.")
            return
        lines = ["👥 *Recent Client Links:*\n━━━━━━━━━━━━━━━━━━━━"]
        for l in links:
            u = utils.format_bytes(l.get("used_bytes", 0))
            lim = utils.format_bytes(l.get("limit_bytes", 0)) if l.get("limit_bytes", 0) > 0 else "∞"
            status = "🟢" if l.get("active", 1) else "🔴"
            lines.append(f"{status} *{l.get('label', 'Node')}*: `{u} / {lim}`\n   UUID: `{l['uuid']}`")
        await send_message(chat_id, "\n".join(lines))

    elif cmd == "/new":
        if not is_admin:
            await send_message(chat_id, "⛔ Access denied.")
            return
        if len(parts) < 3:
            await send_message(chat_id, "Usage: `/new <name> <limit_in_gb> [days]`\nExample: `/new Alice 50 30`")
            return
        name = parts[1].strip()
        try:
            gb = float(parts[2].strip())
            limit_bytes = int(gb * 1024 * 1024 * 1024)
        except ValueError:
            await send_message(chat_id, "❌ Invalid traffic limit (must be a number).")
            return

        expires_at = None
        if len(parts) >= 4:
            try:
                days = int(parts[3].strip())
                expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
            except ValueError:
                pass

        secret = db.get_or_create_secret_key()
        new_uuid = utils.generate_uuid(secret)
        db.add_link(
            uuid=new_uuid,
            label=name,
            limit_bytes=limit_bytes,
            used_bytes=0,
            active=True,
            created_at=datetime.utcnow().isoformat(),
            expires_at=expires_at,
        )

        vless_link = utils.generate_vless_link(new_uuid, domain, remark=f"MeyREN-{name}")
        sub_link = f"https://{domain}/sub/{new_uuid}"

        reply = (
            "✅ *User Created Successfully!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Name:* `{name}`\n"
            f"🔑 *UUID:* `{new_uuid}`\n"
            f"🎯 *Quota:* `{gb} GB`\n"
            f"📅 *Expires:* `{days if len(parts) >= 4 else 'Never'} days`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 *VLESS Config:*\n`{vless_link}`\n\n"
            f"🌐 *Subscription URL:*\n`{sub_link}`"
        )
        await send_message(chat_id, reply)

    elif cmd == "/del":
        if not is_admin:
            await send_message(chat_id, "⛔ Access denied.")
            return
        if len(parts) < 2:
            await send_message(chat_id, "Usage: `/del <uuid>`")
            return
        uid = parts[1].strip()
        if not db.get_link(uid):
            await send_message(chat_id, "❌ User not found.")
            return
        db.delete_link(uid)
        await send_message(chat_id, f"✅ User `{uid}` has been deleted.")


async def _polling_loop(domain: str, start_time: float):
    global _running
    _running = True
    last_update_id = 0
    logger.info("Telegram companion bot polling started.")

    async with httpx.AsyncClient(timeout=35.0) as client:
        while _running:
            try:
                params = {"timeout": 30, "offset": last_update_id + 1}
                resp = await client.get(f"{API_URL}/getUpdates", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    updates = data.get("result", [])
                    for u in updates:
                        last_update_id = max(last_update_id, u.get("update_id", 0))
                        asyncio.create_task(handle_update(u, domain, start_time))
                elif resp.status_code in (401, 404):
                    logger.error("Invalid Telegram bot token. Disabling bot.")
                    break
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Telegram polling error: {e}")
                await asyncio.sleep(5)

    _running = False


def start_telegram_bot(domain: str, start_time: float):
    global _poll_task
    if not BOT_TOKEN:
        logger.info("TELEGRAM_BOT_TOKEN not provided; Telegram companion bot disabled.")
        return
    _poll_task = asyncio.create_task(_polling_loop(domain, start_time))


def stop_telegram_bot():
    global _running, _poll_task
    _running = False
    if _poll_task:
        _poll_task.cancel()

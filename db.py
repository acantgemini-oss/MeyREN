import sqlite3
import hashlib
import threading
from collections import defaultdict
import secrets
import json
import time
from datetime import datetime, timezone

DB_FILE = "meyren.db"

_LINKS_CACHE: dict = {}
_SUB_TOKEN_CACHE: dict = {}
_PENDING_USAGE: dict = defaultdict(int)
_CACHE_LOCK = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-2000;")  # ~2MB cache cap
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def hash_password(pw: str, secret_key: str) -> str:
    return hashlib.sha256(f"{pw}{secret_key}".encode()).hexdigest()


def get_or_create_secret_key(env_secret: str | None = None) -> str:
    """Returns persistent secret key from env or DB settings, creating and saving one if missing."""
    conn = _get_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
    
    if env_secret and env_secret.strip():
        secret = env_secret.strip()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('secret_key', ?)", (secret,))
        conn.commit()
        conn.close()
        return secret

    c.execute("SELECT value FROM settings WHERE key='secret_key'")
    row = c.fetchone()
    if row and row[0]:
        secret = row[0]
        conn.close()
        return secret

    # Generate new persistent secret and store it
    secret = secrets.token_urlsafe(32)
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('secret_key', ?)", (secret,))
    conn.commit()
    conn.close()
    return secret


def init_db(secret_key: str):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
    
    # 1. Base links table
    c.execute("""CREATE TABLE IF NOT EXISTS links (
        uuid TEXT PRIMARY KEY,
        label TEXT,
        limit_bytes INTEGER,
        used_bytes INTEGER,
        active INTEGER,
        created_at TEXT
    )""")

    # 2. Automated Schema Migrations for links table
    c.execute("PRAGMA table_info(links)")
    existing_cols = {row[1] for row in c.fetchall()}

    new_cols = [
        ("speed_limit_mbps", "REAL DEFAULT 0.0"),
        ("max_ips", "INTEGER DEFAULT 0"),
        ("expires_at", "TEXT DEFAULT NULL"),
        ("sub_token", "TEXT DEFAULT NULL"),
        ("group_id", "INTEGER DEFAULT 1"),
        ("last_connected_at", "TEXT DEFAULT NULL"),
    ]

    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            c.execute(f"ALTER TABLE links ADD COLUMN {col_name} {col_type}")

    # Create index on sub_token for fast lookup
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_links_sub_token ON links(sub_token) WHERE sub_token IS NOT NULL")

    # 3. New Categories table
    c.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("INSERT OR IGNORE INTO categories (id, name, description) VALUES (1, 'Default', 'Standard configurations')")

    # 4. New Multi-Admin RBAC table
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        permissions TEXT NOT NULL DEFAULT '["all"]',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # 5. Announcements table
    c.execute("""CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # 6. Audit Logs table
    c.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        ip_address TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Settings initialization
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)", (secret_key,))

    # Ensure master admin password in settings and admins table
    c.execute("SELECT value FROM settings WHERE key='password_hash'")
    pw_row = c.fetchone()
    if not pw_row:
        admin_pw = "admin"
        default_hash = hash_password(admin_pw, secret_key)
        c.execute("INSERT INTO settings (key, value) VALUES ('password_hash', ?)", (default_hash,))
    else:
        default_hash = pw_row[0]

    # Seed default superadmin in admins table if not present
    c.execute("SELECT id FROM admins WHERE username='admin'")
    if not c.fetchone():
        c.execute(
            "INSERT INTO admins (username, password_hash, role, permissions, is_active) VALUES (?, ?, 'superadmin', ?, 1)",
            ("admin", default_hash, json.dumps(["all"]))
        )

    # Auto-generate sub_token for existing links missing one
    c.execute("SELECT uuid FROM links WHERE sub_token IS NULL OR sub_token = ''")
    missing_tokens = c.fetchall()
    for (m_uuid,) in missing_tokens:
        tok = secrets.token_urlsafe(16)
        c.execute("UPDATE links SET sub_token=? WHERE uuid=?", (tok, m_uuid))

    conn.commit()

    # Load in-memory link cache
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM links")
    rows = c.fetchall()
    conn.close()

    with _CACHE_LOCK:
        _LINKS_CACHE.clear()
        _SUB_TOKEN_CACHE.clear()
        _PENDING_USAGE.clear()
        for row in rows:
            d = dict(row)
            # Ensure defaults for nullable fields
            d["speed_limit_mbps"] = float(d.get("speed_limit_mbps") or 0.0)
            d["max_ips"] = int(d.get("max_ips") or 0)
            d["group_id"] = int(d.get("group_id") or 1)
            _LINKS_CACHE[d["uuid"]] = d
            if d.get("sub_token"):
                _SUB_TOKEN_CACHE[d["sub_token"]] = d["uuid"]


def reset_admin_password(new_password: str = "admin", secret_key: str | None = None) -> str:
    """Resets the admin password in DB and returns the new hash."""
    if not secret_key:
        secret_key = get_or_create_secret_key()
    new_hash = hash_password(new_password, secret_key)
    update_admin_password_hash(new_hash)
    # Also update 'admin' entry in admins table
    conn = _get_connection()
    c = conn.cursor()
    c.execute("UPDATE admins SET password_hash=? WHERE username='admin'", (new_hash,))
    conn.commit()
    conn.close()
    return new_hash


def get_admin_password_hash() -> str:
    conn = _get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='password_hash'")
    result = c.fetchone()
    conn.close()
    return result[0] if result else ""


def update_admin_password_hash(new_hash: str):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("UPDATE settings SET value=? WHERE key='password_hash'", (new_hash,))
    c.execute("UPDATE admins SET password_hash=? WHERE username='admin'", (new_hash,))
    conn.commit()
    conn.close()


def add_link(
    uuid: str,
    label: str,
    limit_bytes: int,
    used_bytes: int,
    active: bool,
    created_at: str,
    speed_limit_mbps: float = 0.0,
    max_ips: int = 0,
    expires_at: str | None = None,
    sub_token: str | None = None,
    group_id: int = 1,
):
    if not sub_token:
        sub_token = secrets.token_urlsafe(16)

    conn = _get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO links (
            uuid, label, limit_bytes, used_bytes, active, created_at,
            speed_limit_mbps, max_ips, expires_at, sub_token, group_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            uuid, label, limit_bytes, used_bytes, int(active), created_at,
            speed_limit_mbps, max_ips, expires_at, sub_token, group_id
        ),
    )
    conn.commit()
    conn.close()

    with _CACHE_LOCK:
        link_dict = {
            "uuid": uuid,
            "label": label,
            "limit_bytes": limit_bytes,
            "used_bytes": used_bytes,
            "active": int(active),
            "created_at": created_at,
            "speed_limit_mbps": speed_limit_mbps,
            "max_ips": max_ips,
            "expires_at": expires_at,
            "sub_token": sub_token,
            "group_id": group_id,
            "last_connected_at": None,
        }
        _LINKS_CACHE[uuid] = link_dict
        _SUB_TOKEN_CACHE[sub_token] = uuid


def get_links() -> list:
    with _CACHE_LOCK:
        return [dict(link) for link in _LINKS_CACHE.values()]


def get_link(uuid: str) -> dict | None:
    with _CACHE_LOCK:
        link = _LINKS_CACHE.get(uuid)
        return dict(link) if link is not None else None


def get_link_by_sub_token(sub_token: str) -> dict | None:
    with _CACHE_LOCK:
        uuid = _SUB_TOKEN_CACHE.get(sub_token)
        if not uuid:
            return None
        link = _LINKS_CACHE.get(uuid)
        return dict(link) if link is not None else None


def check_quota_fast(uuid: str, extra_bytes: int = 0) -> bool:
    with _CACHE_LOCK:
        link = _LINKS_CACHE.get(uuid)
        if link is None:
            return False
        if not link.get("active", 1):
            return False
        
        # Check expiration date
        expires_at = link.get("expires_at")
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(exp_dt.tzinfo or None) > exp_dt:
                    return False
            except Exception:
                pass

        limit_bytes = link.get("limit_bytes", 0)
        if limit_bytes == 0:
            return True
        return (link.get("used_bytes", 0) + extra_bytes) <= limit_bytes


def add_usage_buffered(uuid: str, extra_bytes: int):
    with _CACHE_LOCK:
        link = _LINKS_CACHE.get(uuid)
        if link is not None:
            link["used_bytes"] = link.get("used_bytes", 0) + extra_bytes
        _PENDING_USAGE[uuid] += extra_bytes


def add_usage(uuid: str, extra_bytes: int):
    add_usage_buffered(uuid, extra_bytes)


def flush_usage_to_db():
    with _CACHE_LOCK:
        if not _PENDING_USAGE:
            return
        items = list(_PENDING_USAGE.items())
        _PENDING_USAGE.clear()

    if not items:
        return

    try:
        conn = _get_connection()
        c = conn.cursor()
        c.executemany(
            "UPDATE links SET used_bytes = used_bytes + ? WHERE uuid = ?",
            [(bytes_delta, uid) for uid, bytes_delta in items if bytes_delta > 0],
        )
        conn.commit()
        conn.close()
    except Exception as e:
        with _CACHE_LOCK:
            for uid, bytes_delta in items:
                _PENDING_USAGE[uid] += bytes_delta
        raise e


def touch_last_connected(uuid: str):
    now_iso = datetime.now(timezone.utc).isoformat()
    with _CACHE_LOCK:
        link = _LINKS_CACHE.get(uuid)
        if link is not None:
            link["last_connected_at"] = now_iso

    try:
        conn = _get_connection()
        c = conn.cursor()
        c.execute("UPDATE links SET last_connected_at=? WHERE uuid=?", (now_iso, uuid))
        conn.commit()
        conn.close()
    except Exception:
        pass


def update_link(
    uuid: str,
    active: bool = None,
    limit_bytes: int = None,
    reset_usage: bool = False,
    label: str = None,
    speed_limit_mbps: float = None,
    max_ips: int = None,
    expires_at: str | None = ...,
    group_id: int = None,
    sub_token: str = None,
):
    if reset_usage:
        with _CACHE_LOCK:
            _PENDING_USAGE.pop(uuid, None)

    conn = _get_connection()
    c = conn.cursor()

    updates = []
    params = []

    if active is not None:
        updates.append("active=?")
        params.append(int(active))
    if limit_bytes is not None:
        updates.append("limit_bytes=?")
        params.append(limit_bytes)
    if reset_usage:
        updates.append("used_bytes=0")
    if label is not None:
        updates.append("label=?")
        params.append(label)
    if speed_limit_mbps is not None:
        updates.append("speed_limit_mbps=?")
        params.append(float(speed_limit_mbps))
    if max_ips is not None:
        updates.append("max_ips=?")
        params.append(int(max_ips))
    if expires_at is not ...:
        updates.append("expires_at=?")
        params.append(expires_at)
    if group_id is not None:
        updates.append("group_id=?")
        params.append(int(group_id))
    if sub_token is not None:
        updates.append("sub_token=?")
        params.append(sub_token)

    if updates:
        params.append(uuid)
        c.execute(f"UPDATE links SET {', '.join(updates)} WHERE uuid=?", tuple(params))
        conn.commit()
    conn.close()

    with _CACHE_LOCK:
        link = _LINKS_CACHE.get(uuid)
        if link is not None:
            if active is not None:
                link["active"] = int(active)
            if limit_bytes is not None:
                link["limit_bytes"] = limit_bytes
            if reset_usage:
                link["used_bytes"] = 0
            if label is not None:
                link["label"] = label
            if speed_limit_mbps is not None:
                link["speed_limit_mbps"] = float(speed_limit_mbps)
            if max_ips is not None:
                link["max_ips"] = int(max_ips)
            if expires_at is not ...:
                link["expires_at"] = expires_at
            if group_id is not None:
                link["group_id"] = int(group_id)
            if sub_token is not None:
                old_tok = link.get("sub_token")
                if old_tok and old_tok in _SUB_TOKEN_CACHE:
                    _SUB_TOKEN_CACHE.pop(old_tok, None)
                link["sub_token"] = sub_token
                _SUB_TOKEN_CACHE[sub_token] = uuid


def delete_link(uuid: str):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM links WHERE uuid=?", (uuid,))
    conn.commit()
    conn.close()

    with _CACHE_LOCK:
        link = _LINKS_CACHE.pop(uuid, None)
        if link and link.get("sub_token"):
            _SUB_TOKEN_CACHE.pop(link["sub_token"], None)
        _PENDING_USAGE.pop(uuid, None)


# ══════════════════════════════════════════════════════════════════════════════
# Categories / Groups CRUD
# ══════════════════════════════════════════════════════════════════════════════

def get_categories() -> list:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM categories ORDER BY id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def add_category(name: str, description: str = "") -> int:
    conn = _get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
    cat_id = c.lastrowid
    conn.commit()
    conn.close()
    return cat_id


def delete_category(cat_id: int):
    if cat_id == 1:
        raise ValueError("Cannot delete default category")
    conn = _get_connection()
    c = conn.cursor()
    # Reassign links in this group to default group (1)
    c.execute("UPDATE links SET group_id=1 WHERE group_id=?", (cat_id,))
    c.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()

    with _CACHE_LOCK:
        for link in _LINKS_CACHE.values():
            if link.get("group_id") == cat_id:
                link["group_id"] = 1


# ══════════════════════════════════════════════════════════════════════════════
# Multi-Admin RBAC CRUD
# ══════════════════════════════════════════════════════════════════════════════

def get_admins() -> list:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, username, role, permissions, is_active, created_at FROM admins ORDER BY id ASC")
    rows = []
    for r in c.fetchall():
        d = dict(r)
        try:
            d["permissions"] = json.loads(d["permissions"])
        except Exception:
            d["permissions"] = []
        rows.append(d)
    conn.close()
    return rows


def get_admin_by_username(username: str) -> dict | None:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM admins WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["permissions"] = json.loads(d["permissions"])
    except Exception:
        d["permissions"] = []
    return d


def add_admin(username: str, password_hash: str, role: str = "admin", permissions: list = None) -> int:
    if permissions is None:
        permissions = ["manage_users", "view_stats"] if role != "superadmin" else ["all"]
    conn = _get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO admins (username, password_hash, role, permissions, is_active) VALUES (?, ?, ?, ?, 1)",
        (username, password_hash, role, json.dumps(permissions)),
    )
    admin_id = c.lastrowid
    conn.commit()
    conn.close()
    return admin_id


def update_admin(admin_id: int, password_hash: str = None, role: str = None, permissions: list = None, is_active: bool = None):
    conn = _get_connection()
    c = conn.cursor()
    updates = []
    params = []
    if password_hash is not None:
        updates.append("password_hash=?")
        params.append(password_hash)
    if role is not None:
        updates.append("role=?")
        params.append(role)
    if permissions is not None:
        updates.append("permissions=?")
        params.append(json.dumps(permissions))
    if is_active is not None:
        updates.append("is_active=?")
        params.append(int(is_active))

    if updates:
        params.append(admin_id)
        c.execute(f"UPDATE admins SET {', '.join(updates)} WHERE id=?", tuple(params))
        conn.commit()
    conn.close()


def delete_admin(admin_id: int):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM admins WHERE id=?", (admin_id,))
    row = c.fetchone()
    if row and row[0] == "admin":
        conn.close()
        raise ValueError("Cannot delete root admin account")
    c.execute("DELETE FROM admins WHERE id=?", (admin_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Announcements CRUD
# ══════════════════════════════════════════════════════════════════════════════

def get_announcements(active_only: bool = False) -> list:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if active_only:
        c.execute("SELECT * FROM announcements WHERE is_active=1 ORDER BY id DESC")
    else:
        c.execute("SELECT * FROM announcements ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def add_announcement(title: str, content: str, level: str = "info") -> int:
    conn = _get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO announcements (title, content, level, is_active) VALUES (?, ?, ?, 1)",
        (title, content, level),
    )
    ann_id = c.lastrowid
    conn.commit()
    conn.close()
    return ann_id


def toggle_announcement(ann_id: int, is_active: bool):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("UPDATE announcements SET is_active=? WHERE id=?", (int(is_active), ann_id))
    conn.commit()
    conn.close()


def delete_announcement(ann_id: int):
    conn = _get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM announcements WHERE id=?", (ann_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Audit Logging
# ══════════════════════════════════════════════════════════════════════════════

def add_audit_log(admin_user: str, action: str, target: str = "", ip_address: str = ""):
    try:
        conn = _get_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO audit_logs (admin_user, action, target, ip_address) VALUES (?, ?, ?, ?)",
            (admin_user, action, target, ip_address),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_audit_logs(limit: int = 100) -> list:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
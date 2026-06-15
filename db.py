import sqlite3
import hashlib
import os

DB_FILE = "meyren.db"

def init_db(secret_key: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS links (uuid TEXT PRIMARY KEY, label TEXT, limit_bytes INTEGER, used_bytes INTEGER, active INTEGER, created_at TEXT)''')
    
    c.execute("SELECT value FROM settings WHERE key='password_hash'")
    if not c.fetchone():
        default_hash = hashlib.sha256(f"{os.environ.get('ADMIN_PASSWORD', 'admin')}{secret_key}".encode()).hexdigest()
        c.execute("INSERT INTO settings (key, value) VALUES ('password_hash', ?)", (default_hash,))
        
    conn.commit()
    conn.close()

def get_admin_password_hash() -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='password_hash'")
    result = c.fetchone()
    conn.close()
    return result[0] if result else ""

def update_admin_password_hash(new_hash: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE settings SET value=? WHERE key='password_hash'", (new_hash,))
    conn.commit()
    conn.close()
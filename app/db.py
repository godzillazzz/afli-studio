"""SQLite storage: settings + jobs. Single file, no server needed."""
import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "afli.db")
_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "anthropic_api_key": "",
    "llm_model": "claude-haiku-5-5",
    "tts_voice": "th-TH-PremwadeeNeural",
    "tts_rate": "+12%",
    "fal_key": "",
    "fal_model": "fal-ai/kling-video/v2.1/standard/image-to-video",
    "ai_clip_count": "2",
    "ai_provider": "fal",
    "gemini_api_key": "",
    "veo_model": "veo-3.1-fast-generate-preview",
    "fb_page_id": "",
    "fb_page_token": "",
    "tiktok_client_key": "",
    "tiktok_client_secret": "",
    "tiktok_access_token": "",
    "tiktok_refresh_token": "",
    "tiktok_token_expires": "0",
    "public_url": "",
    "default_mode": "slideshow",
    "auto_post_fb": "0",
    "auto_post_tiktok": "0",
    "music_volume": "0.12",
}

SECRET_KEYS = {"anthropic_api_key", "fal_key", "gemini_api_key", "fb_page_token", "tiktok_client_secret",
               "tiktok_access_token", "tiktok_refresh_token"}


def conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created REAL, updated REAL,
            url TEXT, mode TEXT, status TEXT, step TEXT, progress INTEGER DEFAULT 0,
            error TEXT, product TEXT, script TEXT, caption TEXT,
            video TEXT, thumb TEXT, cost REAL DEFAULT 0,
            post_fb INTEGER DEFAULT 0, post_tiktok INTEGER DEFAULT 0,
            fb_status TEXT, tt_status TEXT, log TEXT DEFAULT '',
            reuse_script INTEGER DEFAULT 0)""")
        # jobs interrupted by a restart go back to the queue
        c.execute("UPDATE jobs SET status='queued' WHERE status='running'")


def get_settings():
    with conn() as c:
        rows = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM settings")}
    s = dict(DEFAULT_SETTINGS)
    s.update(rows)
    return s


def set_settings(values: dict):
    with _lock, conn() as c:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (k, str(v)))


def create_job(**fields):
    now = time.time()
    fields.setdefault("status", "queued")
    fields.setdefault("step", "รอคิว")
    fields["created"] = fields["updated"] = now
    keys = ",".join(fields)
    qs = ",".join("?" for _ in fields)
    with _lock, conn() as c:
        cur = c.execute(f"INSERT INTO jobs ({keys}) VALUES ({qs})", list(fields.values()))
        return cur.lastrowid


def update_job(job_id, **fields):
    fields["updated"] = time.time()
    for k in ("product", "script"):
        if k in fields and not isinstance(fields[k], str) and fields[k] is not None:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, conn() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])


def append_log(job_id, line):
    stamp = time.strftime("%H:%M:%S")
    with _lock, conn() as c:
        c.execute("UPDATE jobs SET log = COALESCE(log,'') || ? WHERE id=?", (f"[{stamp}] {line}\n", job_id))


def get_job(job_id):
    with conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row(r) if r else None


def list_jobs(limit=50):
    with conn() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def next_queued():
    with _lock, conn() as c:
        r = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not r:
            return None
        c.execute("UPDATE jobs SET status='running' WHERE id=?", (r["id"],))
        return r["id"]


def delete_job(job_id):
    with _lock, conn() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def _row(r):
    d = dict(r)
    for k in ("product", "script"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except Exception:
            pass
    return d

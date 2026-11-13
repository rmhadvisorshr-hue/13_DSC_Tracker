"""SQLite store (dsc_tracker.db) + optional Supabase/REST sync."""
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

import requests

_HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
DB_PATH = _HERE / "dsc_tracker.db"


def _load_env(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) from the folder next to the .exe / script."""
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env(_HERE / ".env")

# --- Cloud sync: the .exe talks only to our cloud_api (never to Neon directly) ---
CLOUD_API_URL = os.getenv("CLOUD_API_URL", "https://dsc-tracker-api.onrender.com").rstrip("/")  # set in .env to override
CLOUD_TIMEOUT = 60  # free hosts sleep when idle; the first request can take ~50 s to wake them

FIELDS = ["serial_number", "holder_name", "issuer", "issue_date", "expiry_date",
          "box_slot", "client_phone", "status", "notes", "updated_at"]

_COLS = ",".join(FIELDS)  # explicit list: a legacy token_pin column in an old DB is never read
_SCHEMA = """CREATE TABLE IF NOT EXISTS dsc (
    serial_number TEXT PRIMARY KEY, holder_name TEXT, issuer TEXT, issue_date TEXT,
    expiry_date TEXT, box_slot TEXT, client_phone TEXT, status TEXT,
    notes TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS pending_deletes (serial_number TEXT PRIMARY KEY)"""  # deletes waiting to be mirrored to the cloud


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def get(serial: str) -> dict | None:
    with _conn() as c:
        r = c.execute(f"SELECT {_COLS} FROM dsc WHERE serial_number=?", (serial,)).fetchone()
    return dict(r) if r else None


def all_rows() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(f"SELECT {_COLS} FROM dsc ORDER BY expiry_date")]


def upsert(rec: dict) -> None:
    rec = {**rec, "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    cols = ",".join(FIELDS)
    marks = ",".join("?" * len(FIELDS))
    with _conn() as c:  # `with` commits
        c.execute(f"INSERT OR REPLACE INTO dsc ({cols}) VALUES ({marks})",
                  [rec.get(f, "") for f in FIELDS])
        c.execute("DELETE FROM pending_deletes WHERE serial_number=?", (rec["serial_number"],))  # re-saved after delete


def delete(serial: str) -> bool:
    with _conn() as c:
        gone = c.execute("DELETE FROM dsc WHERE serial_number=?", (serial,)).rowcount > 0
        if gone:
            c.execute("INSERT OR IGNORE INTO pending_deletes VALUES (?)", (serial,))
    return gone


# --- login (only the JWT is stored locally, never the password) ---
def _setting(key: str) -> str:
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else ""


def _set_settings(**kv) -> None:
    with _conn() as c:
        for k, v in kv.items():
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))


def is_logged_in() -> bool:
    return bool(_setting("token"))


def auth_status() -> dict:
    return {"logged_in": is_logged_in(), "email": _setting("email")}


def login(email: str, password: str) -> None:
    r = requests.post(CLOUD_API_URL + "/auth/login", json={"email": email, "password": password}, timeout=CLOUD_TIMEOUT)
    if r.status_code == 401:
        raise PermissionError("Wrong email or password")
    r.raise_for_status()
    _set_settings(token=r.json()["token"], email=email)


def logout() -> None:
    _set_settings(token="", email="")


def _cloud(method: str, path: str, **kw) -> requests.Response:
    resp = requests.request(method, CLOUD_API_URL + path, headers={"Authorization": "Bearer " + _setting("token")},
                            timeout=CLOUD_TIMEOUT, **kw)
    if resp.status_code in (401, 403):  # expired/invalid token
        logout()
        raise PermissionError("Cloud login expired - please log in again")
    resp.raise_for_status()
    return resp


def flush_deletes() -> None:
    with _conn() as c:
        pending = [r[0] for r in c.execute("SELECT serial_number FROM pending_deletes")]
    for serial in pending:
        _cloud("DELETE", f"/records/{serial}")
        with _conn() as c:
            c.execute("DELETE FROM pending_deletes WHERE serial_number=?", (serial,))


def sync_to_cloud(rows: list[dict] | None = None) -> int:
    """Mirror queued deletes, then upsert `rows` (default: every local row). Returns rows sent."""
    if not is_logged_in():
        raise RuntimeError("Not logged in to the cloud. Log in first.")
    flush_deletes()
    rows = all_rows() if rows is None else rows
    if rows:
        _cloud("POST", "/records/upsert", json=rows)
    return len(rows)


def pull_from_cloud() -> int:
    """Upsert every cloud record into local SQLite (cloud's updated_at kept), skipping serials with a queued delete."""
    if not is_logged_in():
        raise RuntimeError("Not logged in to the cloud. Log in first.")
    recs = _cloud("GET", "/records").json()
    with _conn() as c:
        skip = {r[0] for r in c.execute("SELECT serial_number FROM pending_deletes")}
        rows = [[str(rec.get(f) or "") for f in FIELDS] for rec in recs if rec.get("serial_number") not in skip]
        c.executemany(f"INSERT OR REPLACE INTO dsc ({_COLS}) VALUES ({','.join('?' * len(FIELDS))})", rows)
    return len(rows)

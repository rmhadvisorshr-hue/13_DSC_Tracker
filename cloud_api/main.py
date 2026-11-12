"""Cloud sync API: FastAPI + Neon Postgres. Run: uvicorn main:app --host 0.0.0.0 --port $PORT
The Neon DATABASE_URL lives only in this service's environment, never in the .exe / .apk."""
import datetime as dt
import hmac
import os
from pathlib import Path
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import jwt
import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from pydantic import BaseModel, field_validator

load_dotenv()
load_dotenv(Path(__file__).parent.parent / ".env.local")  # written by `neon deploy` (repo root); real env vars win
DATABASE_URL = os.environ["DATABASE_URL"]
JWT_SECRET = os.environ["JWT_SECRET"]
USER_EMAIL = os.environ["APP_USER_EMAIL"].strip().lower()
USER_PASSWORD = os.environ["APP_USER_PASSWORD"]
TOKEN_DAYS = 90  # long-lived so the offline-first desktop app rarely asks to log in again
EXPIRING_DAYS = 30
IST = ZoneInfo("Asia/Kolkata")

COLS = ["serial_number", "holder_name", "issuer", "issue_date", "expiry_date",
        "box_slot", "client_phone", "status", "notes"]
SCHEMA = """CREATE TABLE IF NOT EXISTS dsc_records (
    serial_number text PRIMARY KEY, holder_name text NOT NULL, issuer text NOT NULL DEFAULT '',
    issue_date date NOT NULL, expiry_date date NOT NULL, box_slot text NOT NULL,
    client_phone text NOT NULL DEFAULT '', status text NOT NULL DEFAULT 'In Office',
    notes text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now())"""
UPSERT = (f"INSERT INTO dsc_records ({','.join(COLS)}) VALUES ({','.join(f'%({c})s' for c in COLS)}) "
          f"ON CONFLICT (serial_number) DO UPDATE SET "
          f"{','.join(f'{c}=EXCLUDED.{c}' for c in COLS[1:])}, updated_at=now()")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)  # `with db() as c` commits on exit


@asynccontextmanager
async def lifespan(_app):
    with db() as c:
        c.execute(SCHEMA)
    yield


app = FastAPI(title="RMH DSC Cloud API", lifespan=lifespan)
# Bearer-token auth (no cookies), so any origin is safe; the Android webview origin is https://localhost.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer()


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    try:
        return jwt.decode(cred.credentials, JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


class Login(BaseModel):
    email: str
    password: str


class Record(BaseModel):  # extra fields (updated_at, days_remaining, ...) are ignored
    serial_number: str
    holder_name: str
    issuer: str = ""
    issue_date: dt.date
    expiry_date: dt.date
    box_slot: str
    client_phone: str = ""
    status: str = "In Office"
    notes: str = ""

    @field_validator("serial_number")
    @classmethod
    def _serial(cls, v):
        v = v.strip().upper()
        if not v:
            raise ValueError("serial_number required")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in ("In Office", "Handed to Client"):
            raise ValueError("status must be 'In Office' or 'Handed to Client'")
        return v


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/auth/login")
def login(b: Login):
    ok_email = hmac.compare_digest(b.email.strip().lower().encode(), USER_EMAIL.encode())
    ok_pass = hmac.compare_digest(b.password.encode(), USER_PASSWORD.encode())
    if not (ok_email and ok_pass):  # both compared, so timing doesn't reveal which was wrong
        raise HTTPException(401, "Wrong email or password")
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=TOKEN_DAYS)
    return {"token": jwt.encode({"sub": USER_EMAIL, "exp": exp}, JWT_SECRET, algorithm="HS256"), "email": USER_EMAIL}


@app.get("/records")
def list_records(_: str = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT *, updated_at::text AS updated_at FROM dsc_records ORDER BY expiry_date").fetchall()
    today = dt.datetime.now(IST).date()
    for r in rows:
        d = (r["expiry_date"] - today).days
        r["days_remaining"] = d
        r["expiry_status"] = "Expired" if d < 0 else "Expiring Soon" if d <= EXPIRING_DAYS else "Active"
    return rows


@app.post("/records/upsert")
def upsert(body: Record | list[Record], _: str = Depends(current_user)):
    recs = body if isinstance(body, list) else [body]
    if recs:
        with db() as c:
            c.cursor().executemany(UPSERT, [r.model_dump() for r in recs])
    return {"upserted": len(recs)}


@app.delete("/records/{serial_number}")
def delete(serial_number: str, _: str = Depends(current_user)):
    with db() as c:  # idempotent: deleting a missing row is not an error (desktop retries queued deletes)
        n = c.execute("DELETE FROM dsc_records WHERE serial_number=%s", (serial_number.strip().upper(),)).rowcount
    return {"deleted": n}

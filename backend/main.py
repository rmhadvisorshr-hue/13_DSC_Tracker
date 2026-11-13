"""FastAPI server: `uvicorn main:app --port 8000` (run from backend/)."""
import io
import sys
import threading
import webbrowser
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import database as db
import dsc_reader as reader

app = FastAPI(title="RMH DSC Tracker")
app.add_middleware(
    CORSMiddleware,
    # 8000 = bundled app (same origin); 5173/5174 = Vite dev server
    allow_origins=[f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (5173, 5174, 8000)],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Record(BaseModel):
    serial_number: str
    holder_name: str
    issuer: str = ""
    issue_date: str
    expiry_date: str
    box_slot: str
    client_phone: str = ""
    status: str = "In Office"  # In Office | Handed to Client
    notes: str = ""

    @field_validator("serial_number")
    @classmethod
    def _serial(cls, v):
        v = v.strip().upper()
        if not v:
            raise ValueError("serial_number required")
        return v

    @field_validator("box_slot")
    @classmethod
    def _box(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("box_slot required")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in ("In Office", "Handed to Client"):
            raise ValueError("status must be 'In Office' or 'Handed to Client'")
        return v

    @field_validator("issue_date", "expiry_date")
    @classmethod
    def _date(cls, v):
        import datetime as dt
        dt.date.fromisoformat(v)  # ValueError -> 422
        return v


def _enrich(r: dict) -> dict:
    d = reader.days_left(r["expiry_date"])
    return {**r, "days_remaining": d, "expiry_status": reader.status_of(d)}


@app.get("/api/extract")
def extract():
    """{'certificates': [...], 'message': str|None}; message explains an empty result."""
    try:
        r = reader.scan()
    except Exception as e:
        raise HTTPException(500, f"Could not read certificates: {e}")
    for c in r["certificates"]:
        old = db.get(c["serial_number"])
        c["already_registered"] = old is not None
        c["existing_box_slot"] = old["box_slot"] if old else None
    # plugged-in tokens first, then unregistered before registered (stable sort)
    r["certificates"].sort(key=lambda c: (c["source"] != "token", c["already_registered"]))
    return {"certificates": r["certificates"], "message": r["message"], "drivers": r["drivers"]}


@app.get("/api/debug-tokens")
def debug_tokens():
    try:
        return reader.debug()
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/records")
def list_records(q: str = "", filter: str = Query("all", pattern="^(all|expiring|expired|in_office)$")):
    q = q.lower().strip()
    out = []
    for r in map(_enrich, db.all_rows()):
        if q and not any(q in (r[k] or "").lower() for k in ("holder_name", "box_slot", "serial_number")):
            continue
        if filter == "expiring" and r["expiry_status"] != "Expiring Soon":
            continue
        if filter == "expired" and r["expiry_status"] != "Expired":
            continue
        if filter == "in_office" and r["status"] != "In Office":
            continue
        out.append(r)
    return out


def _net_err(e: Exception) -> str:
    return "No internet connection - saved locally, use 'Sync All to Cloud' later" if isinstance(
        e, (requests.ConnectionError, requests.Timeout)) else str(e)


class Login(BaseModel):
    email: str
    password: str


@app.get("/api/auth/status")
def auth_status():
    return db.auth_status()


@app.post("/api/auth/login")
def auth_login(b: Login):
    try:
        db.login(b.email.strip(), b.password)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(502, "Cannot reach the cloud server: " + _net_err(e))
    try:
        db.pull_from_cloud()  # fresh install fills up immediately; a pull failure must not fail the login
    except Exception:
        pass
    return db.auth_status()


@app.post("/api/auth/logout")
def auth_logout():
    db.logout()
    return db.auth_status()


@app.post("/api/records")
def save_record(rec: Record):
    existed = db.get(rec.serial_number) is not None
    db.upsert(rec.model_dump())  # offline-first: local save always happens first
    sync = {"configured": db.is_logged_in(), "ok": None, "error": None}
    if sync["configured"]:  # a sync failure must not lose the local save
        try:
            db.sync_to_cloud([db.get(rec.serial_number)])
            sync["ok"] = True
        except Exception as e:
            sync.update(ok=False, error=_net_err(e))
    return {"updated": existed, "record": _enrich(db.get(rec.serial_number)), "sync": sync}


@app.delete("/api/records/{serial_number}")
def delete_record(serial_number: str):
    serial = serial_number.strip().upper()
    if not db.delete(serial):
        raise HTTPException(404, "Record not found")
    if db.is_logged_in():
        try:
            db.flush_deletes()
        except Exception:
            pass  # stays queued; mirrored on the next save / 'Sync All to Cloud'
    return {"deleted": serial_number}


@app.post("/api/sync")
def sync():
    try:
        pushed = db.sync_to_cloud()  # deletes first, then push
        return {"synced": pushed, "pulled": db.pull_from_cloud()}
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(401 if isinstance(e, PermissionError) else 400, str(e))
    except Exception as e:
        raise HTTPException(502, "Cloud sync failed: " + _net_err(e))


@app.get("/api/export")
def export():
    rows = [_enrich(r) for r in db.all_rows()]
    cols = {"holder_name": "Holder", "box_slot": "Box / Slot", "expiry_status": "Expiry Status",
            "days_remaining": "Days Remaining", "issue_date": "Issue Date", "expiry_date": "Expiry Date",
            "status": "Token Status", "client_phone": "Phone", "issuer": "Issuer",
            "serial_number": "Serial (Hex)", "notes": "Notes"}  # PIN deliberately excluded
    df = pd.DataFrame(rows, columns=list(cols)).rename(columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="DSC Tracker")
        ws = xw.sheets["DSC Tracker"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(
                max(len(str(c.value or "")) for c in col) + 3, 45)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="DSC_Tracker.xlsx"'},
    )


# --- bundled frontend (must be mounted LAST so /api/* routes win) ---
_DIST = (Path(sys._MEIPASS) / "dist") if getattr(sys, "frozen", False) else Path(__file__).parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")  # html=True serves index.html at /

URL = "http://127.0.0.1:8000"


def open_ui():
    """Edge/Chrome in standalone --app window; falls back to the default browser."""
    import os, shutil, subprocess
    pf = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for base in pf:
            exe = os.path.join(base, rel)
            if base and os.path.isfile(exe):
                subprocess.Popen([exe, f"--app={URL}"])
                return
    webbrowser.open(URL)


if __name__ == "__main__":
    import socket
    import uvicorn
    import pystray
    from PIL import Image, ImageDraw

    # single instance: if port 8000 is taken, the app is already running -> just open the UI
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 8000))
    except OSError:
        open_ui()
        sys.exit(0)
    finally:
        probe.close()

    # windowed exe has no stdout/stderr; uvicorn's default logging would crash on them
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_config=None))  # loopback only
    threading.Thread(target=server.run, daemon=True).start()

    icon_img = Image.new("RGB", (64, 64), "#1d4ed8")
    ImageDraw.Draw(icon_img).text((14, 24), "DSC", fill="white")
    tray = pystray.Icon(
        "DSC Tracker", icon_img, "DSC Tracker",
        pystray.Menu(
            pystray.MenuItem("Open DSC Tracker", lambda: open_ui(), default=True),
            pystray.MenuItem("Exit", lambda: (setattr(server, "should_exit", True), tray.stop())),
        ),
    )
    threading.Timer(1.5, open_ui).start()
    tray.run()

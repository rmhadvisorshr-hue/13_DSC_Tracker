# DSC Tracker (RMH Advisors)

```
 DSC_Tracker.exe (office laptop, USB token)  ──HTTPS + JWT──►  cloud_api (Render, free)  ──►  Neon Postgres
 DSC_Tracker.apk (Sir's phone, read-only)    ──HTTPS + JWT──►        ▲ only place that knows DATABASE_URL
        local SQLite (offline-first) ◄── every scan saves here first
```
The Neon password exists **only** in the `cloud_api` host's environment. The .exe and .apk contain just the public API URL.

- `backend/` FastAPI + SQLite + USB token reader (Windows) → packaged as `DSC_Tracker.exe`
- `frontend/` React/Vite/Tailwind: desktop UI, plus a phone UI (`VITE_APP_MODE=mobile`) wrapped by Capacitor
- `cloud_api/` FastAPI + Neon sync service

## 1. Neon (database)
1. neon.com → create a project → **Connect** → copy the **pooled** connection string (`...-pooler...?sslmode=require`).
2. The table is created automatically on first start. To create it by hand (Neon **SQL Editor**) or let the alert engine read it:
```sql
create table if not exists dsc_records (
  serial_number text primary key, holder_name text not null, issuer text not null default '',
  issue_date date not null, expiry_date date not null, box_slot text not null,
  client_phone text not null default '', status text not null default 'In Office',
  notes text not null default '', updated_at timestamptz not null default now());
```
`status` is `In Office` / `Handed to Client`. Expiring soon: `select * from dsc_records where expiry_date <= current_date + 30;`

## 2. Deploy `cloud_api` free on Render
1. Push the repo to GitHub. Render → **New → Web Service** → pick the repo.
2. Root Directory `13_DSC_Tracker/cloud_api` · Runtime Python · Build `pip install -r requirements.txt` · Start `uvicorn main:app --host 0.0.0.0 --port $PORT` · Plan Free.
3. Environment variables (copy from `cloud_api/.env.example`): `DATABASE_URL`, `JWT_SECRET` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`), `APP_USER_EMAIL`, `APP_USER_PASSWORD`, and `PYTHON_VERSION=3.13.5`.
4. Open `https://<service>.onrender.com/health` → `{"ok":true}`. Use this URL as `CLOUD_API_URL` below.

Free Render sleeps after ~15 min idle; the first request then takes up to ~1 minute (apps wait up to 60 s and say so). Local test: `cd cloud_api; pip install -r requirements.txt; copy .env.example .env; uvicorn main:app --port 9000`.
Railway works the same (same start command + env vars). Vercel is a poor fit for this long-running FastAPI app.

API: `POST /auth/login` · `GET /records` · `POST /records/upsert` (one record or a list) · `DELETE /records/{serial}` · `GET /health`. All except login/health need `Authorization: Bearer <jwt>`. One shared login (`APP_USER_EMAIL` / `APP_USER_PASSWORD`); the token lasts 90 days.

## 3. Desktop app (Windows)
Set the production URL once: in `backend/database.py` change the default of `CLOUD_API_URL`, or put `CLOUD_API_URL=https://<service>.onrender.com` in a `.env` next to the .exe.
```
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --port 8000      # dev (or: python main.py)
cd ..\frontend && npm install && npm run dev                  # dev UI -> http://localhost:5173
```
Staff log in once in the app (the JWT is kept in local SQLite `settings`; the password is never stored). "Continue offline" skips login. Every save goes to `dsc_tracker.db` first, then is pushed to the cloud; deletes are queued and mirrored. If the internet was down, press **Sync All to Cloud**.

Build the .exe:
```
cd frontend && npm run build
cd ..\backend
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller.exe --onefile --name DSC_Tracker --add-data "..\frontend\dist;dist" --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto --hidden-import uvicorn.lifespan.on --collect-all PyKCS11 --collect-submodules cryptography main.py
```
Ship `DSC_Tracker.exe` (+ optional `.env` with `CLOUD_API_URL`). `dsc_tracker.db` is created beside it. Back it up. Python bitness must match the token driver DLL (`System32` = 64-bit).
Token PINs are **not stored anywhere** (reading a token needs no PIN).

## 4. Push alerts (Firebase) + Android app
The Capacitor project lives at **`13_DSC_Tracker/android/`** (not inside `frontend/`; see `android.path` in `frontend/capacitor.config.json`).

**Firebase (once)**
1. console.firebase.google.com → Add project (no Analytics needed) → **Add app → Android**, package name **`com.rmhadvisors.dsctracker`** → download **`google-services.json`** → put it at `13_DSC_Tracker\android\app\google-services.json`.
2. Project settings → **Service accounts → Generate new private key** → a `.json` file. Open it, copy the **whole content**.
3. Render → `dsc-tracker-api` → Environment, add:
   - `FIREBASE_SERVICE_ACCOUNT_JSON` = the whole JSON (paste as is)
   - `CRON_SECRET` = a long random string (`python -c "import secrets;print(secrets.token_urlsafe(32))"`)
4. GitHub repo → Settings → Secrets and variables → Actions → add `CLOUD_API_URL` (`https://dsc-tracker-api.onrender.com`) and `CRON_SECRET` (same value as Render).

**How alerts work:** every day at 09:30 IST (`.github/workflows/daily-dsc-alerts.yml`, cron `0 4 * * *`) GitHub calls `POST /notifications/send-daily`. `cloud_api` finds DSCs with 30, 7, 1 or 0 days left (IST) and sends one push per DSC to every phone that logged in (title e.g. "⚠️ DSC Expiring in 7 Days", body "Name — Box A - 14 — expires 19 Nov 2026"). Tapping it opens the app on the *Expiring Soon* tab. Dead tokens are deleted automatically; logging out of the app unregisters the phone.
Test now: Actions tab → *Daily DSC expiry alerts* → **Run workflow**, or call `POST /notifications/test` with the app's login token.

**Build the APK**
```
cd 13_DSC_Tracker\frontend
npm install
npm run build:mobile
npx cap sync android            # copies dist-mobile into ..\android
cd ..\android
gradlew.bat assembleDebug       # or: cd ..\frontend && npx cap open android -> Build > Build APK(s)
```
Output: `13_DSC_Tracker\android\app\build\outputs\apk\debug\app-debug.apk`. Needs Android Studio (JDK 17+ and the Android SDK). Preview the phone UI in a browser (no push): `npx vite --mode mobile`.

firebase is on the rmhadvisors.tech@gmail.com
neon on the rmhadvisorsstaff@gmail.com 
github on the rmh.advisors.hr@gmail.com
dsc-tracker-api on the rmh.advisors.hr@gmail.com
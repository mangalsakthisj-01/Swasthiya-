# SwasthyaGrid — Civic Intelligence & Health-Risk Triage

SwasthyaGrid is a civic case-management application with four role dashboards, an interactive OpenStreetMap-based map, jurisdiction-scoped complaints, administrative hotspot publishing, persistent in-app alerts, field investigation reports, downloadable PDF/CSV reports, AI-assisted evidence triage, CivicPulse clustering, SLA reminders, cross-department dependencies and a hash-linked audit trail.

> **Recommended deployment target: Streamlit Community Cloud.** The primary entry point is `app.py`. It uses the same Python SQLAlchemy service layer as the optional FastAPI service in `api.py`. A React/Vite frontend from the supplied project is retained as an optional legacy frontend; it is not the recommended entry point for the Streamlit deployment.

## What is implemented

| Area | Implemented in this package |
|---|---|
| Authentication & roles | Citizen self-registration; environment-bootstrapped admin; admin-managed officer/representative accounts; PBKDF2 password hashing; server/service-layer role checks; DB-backed login throttling; optional single-use password-reset email |
| Cases | Jurisdiction and category routing; location status `CONFIRMED`, `APPROXIMATE`, or `UNKNOWN`; status transition validation; assignment; critical-case alerts; case history; citizens see their own cases; officers see assigned cases; representatives are scoped to a jurisdiction |
| Real map | Folium/Leaflet over OpenStreetMap tiles, attribution, click selection, user-initiated address search via Nominatim, clustered complaint markers, active hotspots, heatmap from authorised records, polygon drawing, accessible case table, approximate coordinates for representatives |
| Hotspots | Admin-created and editable point/circle/polygon advisories; validity windows; admin publication/closure/expiry; persistent notifications for matching registered jurisdictions; expiry worker; audited edits |
| Investigation | Draft/revised/submitted investigation reports; required officer declaration; before/after attachments; resolution proof check uses a persisted after-action media record; PDF export |
| Evidence | Extension/MIME and image-decode validation; size limits; private local storage for development/Compose or private S3-compatible object storage; authorised attachment download in Streamlit and API |
| AI & CivicPulse | Optional Gemini vision adapter; rules-based fallback; no AI-only rejection; candidate cluster signals with evidence; explicit human review and audit trail; not a disease/outbreak diagnosis |
| Operations | SLA policies, worker for SLA reminders/escalations and hotspot expiry, notification deduplication, cross-department dependency tracking, audit hash-chain verification, health endpoints |
| Reports | Permission-scoped CSV with spreadsheet-formula escaping; formatted investigation PDF; no public reports of personal records |
| API | FastAPI companion service with OpenAPI docs at `/docs`, JWT access tokens, error response shape, health checks, role/object-level authorisation and report/evidence routes |

## Known limitations — read before production use

- **Streamlit Community Cloud is the easiest launch target, not a production government hosting environment.** The app code can start with local SQLite, but Community Cloud local files and uploaded evidence are not durable guarantees. For a real pilot, use a managed PostgreSQL service with PostGIS where available and private S3-compatible storage for evidence. Use a host where you control backups, access logging, data residency and retention for sensitive public records.
- The Streamlit entrypoint and FastAPI companion share one Python service layer. The retained React/Vite UI is an optional legacy code path and is **not** wired to the new SQLAlchemy services; deploy `app.py` for the integrated build described here.
- In-app notifications are persistent. Email password recovery is sent only after SMTP is configured. SMS/WhatsApp/push delivery is not included without a provider integration.
- Browser GPS permission is not directly invoked by the Streamlit component; citizens can click the map, search a place, or explicitly mark a location unknown. Offline case-sync/conflict resolution is not implemented.
- Public tile and geocoding providers have acceptable-use rules and rate limits. For a public pilot, configure a suitable geocoder or paid tile provider if traffic warrants it. Nominatim search is user-triggered and cached; do not use it for bulk geocoding.
- Streamlit’s `init_db()` creates tables on startup for easy setup. For controlled schema upgrades, use Alembic migrations during deployment. The migration files are included, but this environment did not have access to a running PostgreSQL/PostGIS service, so PostGIS migration/spatial-index behaviour must be validated in your hosting environment.
- AI output is only a suggestion for human review. No image authenticity determination or medical diagnosis is produced.
- The automated test suite has **17 passing tests** in this build environment. It covers service behaviour, role/object permissions, persistent notifications and edits, hotspot time validity, investigation evidence checks, file validation, CSV/PDF generation, rate-limited authentication, private evidence downloads and SQLAlchemy persistence. The Python files compile, and a clean SQLite Alembic migration was verified. I could not install Streamlit from the package index or run an interactive Streamlit/browser session here because this execution environment had no network access; the deployed UI therefore still needs a live smoke test after dependencies install.

## Quick start — local development

**Prerequisites:** Python 3.12+ and an internet connection for package installation and map tiles.

### 1. Extract and create a private config

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env` before startup:

- Set `SG_JWT_SECRET` to the generated value (at least 32 characters).
- Set `SG_BOOTSTRAP_ADMIN_EMAIL` and `SG_BOOTSTRAP_ADMIN_PASSWORD` (14+ characters) to create the first administrator.
- For local use, keep `DATABASE_URL=sqlite:///./data/swasthyagrid_app.db` and `APP_ENV=development`.
- Never commit `.env`, the SQLite database, private evidence, or hosting secrets.

### 2. Install and start Streamlit

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Open `http://localhost:8501`. The admin account is created from the environment on first launch; there is no public default admin password. Citizens can self-register. The administrator provisions Field Officer and Representative accounts from **User management**.

### 3. Optional demo-only data

Demo identities and fictional records are never seeded automatically. To generate training data locally, configure an administrator and set:

```dotenv
APP_ENV=development
SG_ENABLE_DEMO_DATA=true
SG_DEMO_PASSWORD=choose-a-development-only-password-of-14-plus-characters
```

Then run `python -m scripts.seed_demo`. The script refuses to run outside development/test modes and labels the case/hotspot records as fictional. Never enable demo seeding on a real deployment.

## Recommended deployment — Streamlit Community Cloud

This is the simplest supported single-app deployment.

1. **Upload to GitHub.** Create a repository, extract this ZIP into it, and push the files. Use GitHub’s web upload if you prefer, or:

   ```bash
   git init
   git add .
   git commit -m "Deploy SwasthyaGrid civic platform"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
   git push -u origin main
   ```

   Confirm `.env`, `.streamlit/secrets.toml`, any `.db`/`.sqlite` file, private upload directories and API keys are **not** committed. They are excluded by `.gitignore`.

2. **Provision persistence first.** Create a managed PostgreSQL database. Prefer a provider/plan with PostGIS support because the production schema uses geometry columns and GiST spatial indexes. Add a database user with least-privilege access and enable TLS. Use a URL in this shape:

   ```text
   postgresql+psycopg://USERNAME:PASSWORD@HOST:5432/DATABASE?sslmode=require
   ```

   URL-encode reserved characters in the username/password, or use a URL-safe password. Keep automatic backups enabled. For real citizen evidence, also configure a private S3-compatible bucket. Do not use a public bucket.

3. In Streamlit Community Cloud, choose **Create app**, select your GitHub repository and branch, and set **Main file path** to `app.py`.

4. Open the app’s **Settings → Secrets** and add the configuration in TOML form (replace all example values; these are placeholders, not usable credentials):

   ```toml
   APP_ENV = "production"
   DATABASE_URL = "postgresql+psycopg://USERNAME:PASSWORD@HOST:5432/DATABASE?sslmode=require"
   SG_JWT_SECRET = "REPLACE_WITH_A_RANDOM_SECRET_AT_LEAST_32_CHARACTERS_LONG"
   SG_BOOTSTRAP_ADMIN_EMAIL = "admin@example.org"
   SG_BOOTSTRAP_ADMIN_PASSWORD = "REPLACE_WITH_A_UNIQUE_14_PLUS_CHARACTER_PASSWORD"
   SG_BOOTSTRAP_ADMIN_NAME = "SwasthyaGrid Administrator"
   SG_BOOTSTRAP_ADMIN_JURISDICTION = "Your city or ward"
   SG_PUBLIC_APP_URL = "https://YOUR-APP-NAME.streamlit.app"
   SG_CONTACT_EMAIL = "your-technical-contact@example.org"
   # Optional vision triage
   GEMINI_API_KEY = ""
   SG_GEMINI_MODEL = "gemini-2.5-flash"
   # Recommended private evidence store for live pilots
   SG_S3_BUCKET = "your-private-bucket"
   SG_S3_REGION = "ap-south-1"
   SG_S3_PREFIX = "swasthyagrid-private-evidence"
   AWS_ACCESS_KEY_ID = ""
   AWS_SECRET_ACCESS_KEY = ""
   # Optional account-recovery SMTP
   SG_SMTP_HOST = ""
   SG_SMTP_PORT = 587
   SG_SMTP_USERNAME = ""
   SG_SMTP_PASSWORD = ""
   SG_SMTP_FROM = ""
   ```

   Configure S3 credentials only for a dedicated, least-privilege private bucket. For non-AWS object storage, also set `SG_S3_ENDPOINT_URL`. Keep the account-recovery SMTP credentials and AI key in app secrets, never in GitHub. The application creates the first admin from the bootstrap secret; after successfully signing in, remove the bootstrap password from secrets (the existing account remains).

5. **Run a live smoke test:** register a fictional citizen in a test jurisdiction, submit a test complaint with an intentionally approximate location, sign in as admin, assign a field officer, publish a test hotspot to the same jurisdiction, confirm the notification appears, save/submit an investigation report with before/after evidence, download CSV/PDF, verify the audit chain, and then delete the test records under your data-retention rules.

**Important persistence note:** Streamlit Community Cloud’s local filesystem is not a durable database or secure evidence archive. Configure PostgreSQL and private object storage before accepting real reports. Do not use the demo SQLite file for production data.

## Local multi-service deployment (PostgreSQL/PostGIS + Streamlit + FastAPI + worker)

The included `compose.yaml` runs the Streamlit app, FastAPI API, PostGIS database and scheduler worker. It is the more suitable starting point for an actual hosted pilot where you can manage network ingress, secrets, storage and backups.

```bash
cp .env.example .env
# Edit .env: set SG_POSTGRES_PASSWORD, SG_JWT_SECRET, and first-admin details.
docker compose up --build -d
```

- Streamlit UI: `http://localhost:8501`
- FastAPI docs: `http://localhost:8000/docs`
- API live check: `http://localhost:8000/health/live`
- API readiness: `http://localhost:8000/health/ready`

Before exposing either service to the public internet, put it behind HTTPS/reverse proxy, restrict API/database inbound access, configure private evidence storage and backups, review logging/retention, and change every bootstrap/placeholder secret. Compose is a deployment foundation, not a substitute for operational security review.

### Database migrations and backups

```bash
# Apply the Alembic revision using the same DATABASE_URL as the target environment.
alembic upgrade head

# Example SQLite local backup
cp data/swasthyagrid_app.db data/swasthyagrid_app.db.backup

# Example PostgreSQL backup (run in an environment with pg_dump and database access)
pg_dump "$DATABASE_URL" --format=custom --file swasthyagrid.backup
# Restore to a new/empty target only after checking the target database and retention plan:
pg_restore --clean --if-exists --dbname "$DATABASE_URL" swasthyagrid.backup
```

The deployed app calls `create_all()` to support first boot. For production releases, apply and review migrations using a controlled deployment step and test restore procedures, instead of relying on automatic table creation as a schema-upgrade strategy.

## FastAPI companion API

When dependencies are installed, run:

```bash
uvicorn api:app --host 127.0.0.1 --port 8000
```

Set `APP_ENV=production` and `SG_JWT_SECRET` before exposing the API. The API docs are at `/docs`. API users receive short-lived bearer tokens; Streamlit login uses the shared password-hashing/database layer and Streamlit session state. Do not assume the Streamlit browser session and API bearer token are interchangeable.

## Tests and checks

This packaged build was checked locally on 29 October 2026: **17 pytest tests passed**, Python syntax compilation passed, and `alembic upgrade head` applied successfully to a fresh SQLite database (15 tables including the Alembic version table). Streamlit itself could not be installed or run in this restricted environment because package-index access was unavailable; PostGIS, S3, SMTP, Gemini, and a browser smoke test still need validation in the chosen hosting environment. See [`docs/test-summary.md`](docs/test-summary.md).

```bash
python -m py_compile app.py api.py backend/*.py tests/*.py
python -m pytest -q
```

For deployment readiness, also install all requirements, launch Streamlit and Uvicorn against a clean database, run the manual smoke test above, and validate PostGIS/S3/SMTP configuration in the actual target environment.

## Architecture and feature notes

See [`docs/feature-register.md`](docs/feature-register.md) for inputs, processing, outputs, failure modes and measurement ideas for CivicPulse, incident fusion, proof-of-resolution, SLA Guardian and department dependencies. See [`docs/deployment-checklist.md`](docs/deployment-checklist.md) before using real data.

## Map and public-health safety

- OpenStreetMap map tiles include attribution. Nominatim search is intended for individual user-initiated lookup, not bulk geocoding.
- A map centre is only an initial viewport. Complaint submissions require a real selected/approximate point or an explicit unknown location; hotspot points require an explicit click.
- A CivicPulse signal is a transparent cluster candidate for human investigation, not a confirmed disease outbreak or medical diagnosis.
- Only publish official, reviewed precautions. Contact local public-health authorities for final wording and escalation rules.

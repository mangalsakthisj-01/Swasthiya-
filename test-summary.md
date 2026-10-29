# SwasthyaGrid package verification summary

Verification date: 2026-10-29

## Checks run

- `pytest -q`: **17 passed**.
- `python -m py_compile app.py api.py backend/*.py scripts/*.py tests/*.py`: passed.
- Fresh SQLite migration test using `DATABASE_URL=sqlite:///... alembic upgrade head`: passed; the schema contained the expected core tables and `alembic_version` (15 tables total).
- Secret-pattern scan over the project files: no obvious live-looking API keys found. Automated tests contain clearly test-only credential strings; these are not deployment credentials.
- Archive exclusion review: the input ZIP's pre-existing SQLite database and all Python/test cache files are excluded from the output archive.

## Not verified in this execution environment

- Streamlit runtime/browser smoke test: Streamlit and `streamlit-folium` were not installed because package-index/network access was unavailable. The entrypoint compiles syntactically, but the deployed UI must be launched and smoke-tested after dependencies install.
- PostgreSQL/PostGIS migrations, geometry columns and GiST indexes: no live PostGIS service was available here. Validate against a clean PostGIS instance before accepting real case data.
- Live Gemini vision calls, S3-compatible private storage, and SMTP password-reset delivery: these depend on credentials and external services and were not exercised end-to-end.
- React/Vite production build: the supplied React frontend is retained as a legacy/optional path. The recommended integrated deployment target is the Streamlit entrypoint (`app.py`), which uses the Python service layer shared with `api.py`.
- Public geocoder/tile policy, hosting data-retention, privacy impact review, and backup/restore drills: deployment-owner checks remain required.

## Important usage boundary

The package is a tested application foundation, not a certification that it is ready for a government production environment without configuration and operational review. Do not upload live citizen data until database persistence, private evidence storage, account bootstrap, HTTPS, monitoring, retention and backups are configured and tested.

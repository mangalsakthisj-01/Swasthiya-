# Pre-deployment checklist

## Secrets and identities
- [ ] Remove example/temporary secrets from hosting config and Git history.
- [ ] Set a unique `SG_JWT_SECRET` of at least 32 characters.
- [ ] Set the initial administrator credentials through hosting secrets; remove the bootstrap password after provisioning.
- [ ] Confirm there are no demo users, demo complaints, test files or personal records in the target database.
- [ ] Restrict repository access and review who can edit Streamlit secrets.

## Data and privacy
- [ ] Use managed PostgreSQL, enable PostGIS, TLS, least-privilege DB credentials and scheduled backups.
- [ ] Use private object storage for evidence; verify public access is blocked.
- [ ] Define retention, deletion, legal-hold and incident-response rules before accepting actual citizen records.
- [ ] Review public-health guidance with responsible municipal/public-health officials.
- [ ] Verify precise coordinates, addresses, citizen feedback and evidence are visible only to roles that need them.

## Operations
- [ ] Apply/verify Alembic migrations against a clean staging database.
- [ ] Check `/health/ready`, Streamlit login, admin creation, registration, case submission and cross-role authorisation.
- [ ] Test hotspot publishing, matching notifications, closure and expiry.
- [ ] Test investigation draft/revision/submission, actual after-action evidence, PDF/CSV export, and audit-chain verification.
- [ ] Configure SMTP if account recovery emails are required; test the link end-to-end.
- [ ] Configure private S3-compatible storage and test evidence upload/download after app restart.
- [ ] Monitor app/database logs and establish a backup restore drill.
- [ ] Use HTTPS and network restrictions when exposing the FastAPI service; do not expose the database to the public internet.

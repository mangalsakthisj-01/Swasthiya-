"""Opt-in development-only fictional data seed. Never runs automatically."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from backend.bootstrap import bootstrap_admin
from backend.database import Complaint, Hotspot, User, get_session_factory, init_db
from backend.services import create_complaint, create_user, publish_hotspot


def main() -> None:
    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env not in {"development", "dev", "test"}:
        raise SystemExit("Refusing to seed demo data outside APP_ENV=development/dev/test.")
    if os.getenv("SG_ENABLE_DEMO_DATA", "false").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Set SG_ENABLE_DEMO_DATA=true explicitly before running this development-only seed script.")
    password = os.getenv("SG_DEMO_PASSWORD", "")
    if len(password) < 14:
        raise SystemExit("Set SG_DEMO_PASSWORD to a development-only password of at least 14 characters.")

    init_db()
    factory = get_session_factory()
    with factory() as db:
        bootstrap_admin(db)
        admin = db.query(User).filter_by(role="ADMIN", is_active=True).first()
        if not admin:
            raise SystemExit("Configure SG_BOOTSTRAP_ADMIN_EMAIL and SG_BOOTSTRAP_ADMIN_PASSWORD before seeding.")
        demo_accounts = [
            ("demo-citizen@example.invalid", "Demo Citizen", "CITIZEN", "Resident Services"),
            ("demo-officer@example.invalid", "Demo Field Officer", "FIELD_OFFICER", "Water & Drainage"),
            ("demo-representative@example.invalid", "Demo Ward Representative", "REPRESENTATIVE", "Public Administration"),
        ]
        users: dict[str, User] = {}
        for email, name, role, department in demo_accounts:
            account = db.query(User).filter_by(email=email).first()
            if account and account.role != role:
                raise SystemExit(f"Existing demo identity {email} has unexpected role {account.role}; inspect it manually.")
            if account is None:
                account = create_user(db, email, password, name, role, admin.jurisdiction, department, actor=admin)
            users[role] = account

        existing_demo_cases = db.query(Complaint).filter_by(is_demo=True).count()
        if existing_demo_cases < 3:
            remaining = 3 - existing_demo_cases
            examples = [
                ("DEMO — Blocked storm drain", "Water & drainage", "Water is pooling beside a pedestrian crossing after rain. Fictional training record.", "HIGH", 11.0168, 76.9558),
                ("DEMO — Waste collection gap", "Waste dumping", "A collection point was not serviced on the scheduled day. Fictional training record.", "MEDIUM", 11.0180, 76.9570),
                ("DEMO — Possible mosquito breeding site", "Vector risk", "Standing water was reported in a public lot. Fictional training record; not a confirmed health finding.", "HIGH", 11.0159, 76.9545),
            ]
            for title, category, description, severity, lat, lon in examples[existing_demo_cases:existing_demo_cases + remaining]:
                create_complaint(db, users["CITIZEN"], title=title, category=category, description=description,
                    severity=severity, department="General", jurisdiction=admin.jurisdiction,
                    latitude=lat, longitude=lon, location_status="APPROXIMATE", is_demo=True)

        has_demo_hotspot = db.query(Hotspot).filter_by(evidence_source="DEMO DATA - FICTIONAL").first()
        if not has_demo_hotspot:
            now = datetime.now(timezone.utc)
            publish_hotspot(db, admin, title="DEMO — Advisory for simulated standing water", category="Vector risk",
                geometry_type="Circle", geometry_geojson={"type": "Point", "coordinates": [76.9558, 11.0168]},
                center_latitude=11.0168, center_longitude=76.9558, radius_m=350, severity="MEDIUM",
                description="Fictional training advisory created by the opt-in demo seeder; not a real public-health alert.",
                evidence_source="DEMO DATA - FICTIONAL",
                precautions="This is fictional training content. Verify all real-world advice with your municipal public-health authority.",
                jurisdiction=admin.jurisdiction, valid_from=now.isoformat(),
                expires_at=(now + timedelta(days=7)).isoformat(), publish=True)

    print("Demo seed complete. The three demo identities share SG_DEMO_PASSWORD; never reuse that password for real accounts.")
    print("Demo identities: demo-citizen@example.invalid, demo-officer@example.invalid, demo-representative@example.invalid")


if __name__ == "__main__":
    main()

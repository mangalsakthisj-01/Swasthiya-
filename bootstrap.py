"""Environment-driven first-admin bootstrap. Never creates a public default admin."""
from __future__ import annotations

import os
from sqlalchemy.orm import Session
from .database import User
from .services import create_user


def bootstrap_admin(db: Session) -> str | None:
    email = os.getenv("SG_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("SG_BOOTSTRAP_ADMIN_PASSWORD", "")
    name = os.getenv("SG_BOOTSTRAP_ADMIN_NAME", "SwasthyaGrid Administrator").strip()
    jurisdiction = os.getenv("SG_BOOTSTRAP_ADMIN_JURISDICTION", "Coimbatore, Tamil Nadu").strip()
    if not email and not password:
        return None
    if not email or not password:
        raise RuntimeError("Set both SG_BOOTSTRAP_ADMIN_EMAIL and SG_BOOTSTRAP_ADMIN_PASSWORD to bootstrap the first administrator.")
    if len(password) < 14:
        raise RuntimeError("SG_BOOTSTRAP_ADMIN_PASSWORD must contain at least 14 characters.")
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.role != "ADMIN":
            raise RuntimeError("The configured bootstrap email already belongs to a non-admin account. Choose another email.")
        return "Administrator already exists; bootstrap did not change its password."
    create_user(db, email, password, name, role="ADMIN", jurisdiction=jurisdiction, department="Municipal Administration")
    return f"Bootstrapped administrator account for {email}. Remove bootstrap secrets after confirming access."

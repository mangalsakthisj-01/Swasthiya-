"""Database-backed throttling and single-use password reset token helpers."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from .database import LoginAttempt, PasswordResetToken, User, iso_now
from .security import hash_password
from .services import uid, log_audit


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def login_is_blocked(db: Session, identifier: str) -> tuple[bool, int]:
    row = db.query(LoginAttempt).filter_by(identifier_hash=_hash(identifier)).first()
    if not row or not row.blocked_until:
        return False, 0
    try:
        remaining = (datetime.fromisoformat(row.blocked_until) - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        remaining = 0
    return remaining > 0, max(0, int(remaining))


def record_login_failure(db: Session, identifier: str, *, max_failures: int = 5, window_minutes: int = 15, block_minutes: int = 15) -> None:
    now = datetime.now(timezone.utc)
    key = _hash(identifier)
    row = db.query(LoginAttempt).filter_by(identifier_hash=key).first()
    if not row:
        row = LoginAttempt(identifier_hash=key, failures=1, window_started_at=now.isoformat(), blocked_until=None, updated_at=iso_now())
        db.add(row)
    else:
        try:
            started = datetime.fromisoformat(row.window_started_at)
        except ValueError:
            started = now - timedelta(minutes=window_minutes + 1)
        if (now - started).total_seconds() > window_minutes * 60:
            row.failures = 1
            row.window_started_at = now.isoformat()
            row.blocked_until = None
        else:
            row.failures += 1
        if row.failures >= max_failures:
            row.blocked_until = (now + timedelta(minutes=block_minutes)).isoformat()
        row.updated_at = iso_now()
    db.commit()


def clear_login_failures(db: Session, identifier: str) -> None:
    db.query(LoginAttempt).filter_by(identifier_hash=_hash(identifier)).delete(synchronize_session=False)
    db.commit()


def create_password_reset_token(db: Session, user: User, lifetime_minutes: int = 30) -> str:
    raw = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)).isoformat()
    row = PasswordResetToken(id=uid(), user_id=user.id, token_hash=token_hash, expires_at=expires, used_at=None, created_at=iso_now())
    db.add(row)
    db.commit()
    return raw


def reset_password(db: Session, token: str, new_password: str) -> bool:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.query(PasswordResetToken).filter_by(token_hash=token_hash, used_at=None).first()
    if not row:
        return False
    try:
        if datetime.fromisoformat(row.expires_at) <= datetime.now(timezone.utc):
            return False
    except ValueError:
        return False
    user = db.query(User).filter_by(id=row.user_id, is_active=True).first()
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    user.updated_at = iso_now()
    row.used_at = iso_now()
    log_audit(db, user, "PASSWORD_RESET_COMPLETED", "USER", user.id, {})
    db.commit()
    return True

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

PASSWORD_ITERATIONS = 390_000
TOKEN_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        calculated = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations), dklen=32
        ).hex()
        return hmac.compare_digest(calculated, digest_hex)
    except (ValueError, TypeError):
        return False


def jwt_secret() -> str:
    secret = os.getenv("SG_JWT_SECRET", "").strip()
    if secret:
        if len(secret) < 32:
            raise RuntimeError("SG_JWT_SECRET must be at least 32 characters.")
        return secret
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
        raise RuntimeError("SG_JWT_SECRET is required in production. Configure it as a hosting secret.")
    # Development-only per-process key; API tokens become invalid on process restart.
    secret = os.getenv("STREAMLIT_RUNTIME_SECRET", "").strip() or os.getenv("SG_DEV_JWT_SECRET", "").strip()
    if not secret:
        secret = secrets.token_urlsafe(48)
        os.environ["SG_DEV_JWT_SECRET"] = secret
    return secret


def create_access_token(user: dict[str, Any], expires_minutes: int = 30) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "name": user["full_name"],
        "role": user["role"],
        "jurisdiction": user.get("jurisdiction", "Unassigned"),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=TOKEN_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, jwt_secret(), algorithms=[TOKEN_ALGORITHM])
    except jwt.PyJWTError:
        return None

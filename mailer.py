"""SMTP adapter for account recovery. Secrets are read only from the environment."""
from __future__ import annotations
import os
import smtplib
from email.message import EmailMessage


def send_password_reset_email(to_address: str, token: str) -> bool:
    host = os.getenv("SG_SMTP_HOST", "").strip()
    user = os.getenv("SG_SMTP_USERNAME", "").strip()
    password = os.getenv("SG_SMTP_PASSWORD", "")
    sender = os.getenv("SG_SMTP_FROM", user).strip()
    base_url = os.getenv("SG_PUBLIC_APP_URL", "").strip().rstrip("/")
    if not (host and sender and base_url):
        return False
    port = int(os.getenv("SG_SMTP_PORT", "587"))
    link = f"{base_url}/?reset_token={token}"
    msg = EmailMessage()
    msg["Subject"] = "Reset your SwasthyaGrid password"
    msg["From"] = sender
    msg["To"] = to_address
    msg.set_content(
        "A password reset was requested for your SwasthyaGrid account.\n\n"
        f"Open this link within 30 minutes to reset your password:\n{link}\n\n"
        "If you did not request this, ignore this message. Do not share this link."
    )
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        if user:
            server.login(user, password)
        server.send_message(msg)
    return True

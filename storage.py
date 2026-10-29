"""Private evidence storage: local development adapter or private S3-compatible bucket."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO

MAX_FILE_BYTES = int(os.getenv("SG_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg",
}


def validate_upload(filename: str, content: bytes, declared_mime: str | None = None) -> tuple[str, str]:
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"Each file must be no larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    safe_name = Path(filename or "upload").name
    ext = Path(safe_name).suffix.lower()
    expected = ALLOWED_EXTENSIONS.get(ext)
    if not expected:
        raise ValueError("Unsupported file type. Upload JPG, PNG, WEBP, MP4, WEBM, MP3, WAV, M4A or OGG.")
    mime = (declared_mime or expected).split(";", 1)[0].strip().lower()
    if mime != expected and not (ext == ".m4a" and mime in {"audio/x-m4a", "audio/mp4"}):
        raise ValueError("File type does not match its extension.")
    # Validate common image files by decoding them, not only trusting their MIME header.
    if expected.startswith("image/"):
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(content)) as im:
                im.verify()
        except Exception as exc:
            raise ValueError("The image file is invalid or corrupted.") from exc
    # Reject obvious executable/script payloads regardless of filename.
    if content[:100].lstrip().lower().startswith((b"<script", b"<!doctype html", b"<html", b"<?php")):
        raise ValueError("The uploaded content does not match an accepted media format.")
    return safe_name[:200], expected


def save_private_upload(filename: str, content: bytes, declared_mime: str | None = None) -> dict[str, str | int]:
    safe_name, mime = validate_upload(filename, content, declared_mime)
    key = f"{uuid.uuid4().hex}{Path(safe_name).suffix.lower()}"
    bucket = os.getenv("SG_S3_BUCKET", "").strip()
    if bucket:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("SG_S3_BUCKET is set but boto3 is not installed.") from exc
        client_kwargs = {}
        endpoint = os.getenv("SG_S3_ENDPOINT_URL", "").strip()
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
        region = os.getenv("SG_S3_REGION", "").strip()
        if region:
            client_kwargs["region_name"] = region
        client = boto3.client("s3", **client_kwargs)
        prefix = os.getenv("SG_S3_PREFIX", "swasthyagrid-private-evidence").strip("/")
        object_key = f"{prefix}/{key}" if prefix else key
        client.put_object(Bucket=bucket, Key=object_key, Body=content, ContentType=mime,
                          ServerSideEncryption=os.getenv("SG_S3_SERVER_SIDE_ENCRYPTION", "AES256"))
        return {"stored_name": f"s3://{bucket}/{object_key}", "original_name": safe_name,
                "mime_type": mime, "byte_size": len(content)}
    root = Path(os.getenv("SG_PRIVATE_STORAGE_DIR", "./data/private_uploads")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / key
    # Exclusive creation prevents accidental overwrite if a generated key ever collides.
    with target.open("xb") as stream:
        stream.write(content)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return {"stored_name": str(target), "original_name": safe_name, "mime_type": mime, "byte_size": len(content)}


def read_private_upload(stored_name: str) -> tuple[bytes, str]:
    """Read a previously stored private upload. Call only after record-level authorization."""
    if not stored_name or len(stored_name) > 1024:
        raise ValueError("Invalid evidence reference.")
    if stored_name.startswith("s3://"):
        from urllib.parse import urlparse
        parsed = urlparse(stored_name)
        configured_bucket = os.getenv("SG_S3_BUCKET", "").strip()
        if not configured_bucket or parsed.netloc != configured_bucket:
            raise PermissionError("Evidence storage bucket does not match the configured private bucket.")
        object_key = parsed.path.lstrip("/")
        prefix = os.getenv("SG_S3_PREFIX", "swasthyagrid-private-evidence").strip("/")
        if not object_key or (prefix and not object_key.startswith(prefix + "/")):
            raise PermissionError("Evidence reference is outside the configured private prefix.")
        kwargs = {}
        endpoint = os.getenv("SG_S3_ENDPOINT_URL", "").strip()
        region = os.getenv("SG_S3_REGION", "").strip()
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if region:
            kwargs["region_name"] = region
        import boto3
        response = boto3.client("s3", **kwargs).get_object(Bucket=configured_bucket, Key=object_key)
        body = response["Body"].read(MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES:
            raise ValueError("Stored evidence exceeds the configured download limit.")
        return body, response.get("ContentType", "application/octet-stream")

    root = Path(os.getenv("SG_PRIVATE_STORAGE_DIR", "./data/private_uploads")).resolve()
    candidate = Path(stored_name).resolve()
    if not candidate.is_relative_to(root):
        raise PermissionError("Evidence reference is outside the private storage directory.")
    if not candidate.is_file():
        raise FileNotFoundError("The private evidence file is not available in this deployment.")
    size = candidate.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("Stored evidence exceeds the configured download limit.")
    return candidate.read_bytes(), _mime_for_path(candidate)


def _mime_for_path(path: Path) -> str:
    return ALLOWED_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")

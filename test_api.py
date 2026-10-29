from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    database_path = tmp_path / "api-integration.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("SG_JWT_SECRET", "test-secret-only-for-automated-tests-0123456789")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SG_BOOTSTRAP_ADMIN_EMAIL", "admin-api@example.test")
    monkeypatch.setenv("SG_BOOTSTRAP_ADMIN_PASSWORD", "SafeAdminPasswordForTest2026!")
    monkeypatch.setenv("SG_BOOTSTRAP_ADMIN_JURISDICTION", "Ward API")
    monkeypatch.setenv("SG_PRIVATE_STORAGE_DIR", str(tmp_path / "private"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import api
    api = importlib.reload(api)
    with TestClient(api.app) as client:
        yield client


def _register(client: TestClient, email: str, full_name: str, jurisdiction: str = "Ward API") -> str:
    response = client.post("/api/auth/register", json={
        "email": email, "password": "SecureCitizenPassword!2026",
        "full_name": full_name, "jurisdiction": jurisdiction,
    })
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_api_auth_and_object_level_permissions(api_client: TestClient):
    client = api_client
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/complaints").status_code == 401

    admin_login = client.post("/api/auth/login", json={
        "email": "admin-api@example.test", "password": "SafeAdminPasswordForTest2026!",
    })
    assert admin_login.status_code == 200, admin_login.text
    admin_token = admin_login.json()["access_token"]

    citizen_a = _register(client, "alice-api@example.test", "Alice API")
    citizen_b = _register(client, "bob-api@example.test", "Bob API")
    created = client.post("/api/complaints", headers={"Authorization": f"Bearer {citizen_a}"}, json={
        "title": "Blocked drainage near school", "category": "Water & drainage",
        "description": "Water is overflowing into the pedestrian lane after rainfall.",
        "severity": "HIGH", "jurisdiction": "Ward API", "latitude": 11.0168,
        "longitude": 76.9558, "location_status": "APPROXIMATE",
        "address_text": "Approximate landmark only",
    })
    assert created.status_code == 201, created.text
    complaint_id = created.json()["id"]
    assert created.json()["department"] == "Water & Drainage"

    private_read = client.get(f"/api/complaints/{complaint_id}", headers={"Authorization": f"Bearer {citizen_b}"})
    assert private_read.status_code == 403, private_read.text
    admin_only = client.get("/api/admin/users", headers={"Authorization": f"Bearer {citizen_a}"})
    assert admin_only.status_code == 403, admin_only.text

    # Upload/download remains private and is scoped to an authorised complaint.
    from PIL import Image
    import io
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (120, 160, 180)).save(image_buffer, format="PNG")
    evidence_upload = client.post(f"/api/complaints/{complaint_id}/evidence",
        headers={"Authorization": f"Bearer {citizen_a}"},
        files={"file": ("site.png", image_buffer.getvalue(), "image/png")})
    assert evidence_upload.status_code == 201, evidence_upload.text
    media_id = evidence_upload.json()["id"]
    evidence_download = client.get(f"/api/complaints/{complaint_id}/evidence/{media_id}/download",
        headers={"Authorization": f"Bearer {citizen_a}"})
    assert evidence_download.status_code == 200 and evidence_download.content.startswith(b"\x89PNG")
    stolen_download = client.get(f"/api/complaints/{complaint_id}/evidence/{media_id}/download",
        headers={"Authorization": f"Bearer {citizen_b}"})
    assert stolen_download.status_code == 403

    admin_view = client.get("/api/complaints", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_view.status_code == 200
    assert any(row["id"] == complaint_id for row in admin_view.json())

    malformed = client.post("/api/complaints", headers={"Authorization": f"Bearer {citizen_a}"}, json={"title": "x"})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_admin_hotspot_edit_endpoint_is_role_protected(api_client: TestClient):
    from datetime import datetime, timedelta, timezone
    client = api_client
    admin_login = client.post("/api/auth/login", json={
        "email": "admin-api@example.test", "password": "SafeAdminPasswordForTest2026!",
    })
    assert admin_login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    citizen_token = _register(client, "hotspot-citizen@example.test", "Hotspot Citizen")
    now = datetime.now(timezone.utc)
    payload = {
        "title": "Drainage advisory", "category": "Water & drainage", "geometry_type": "Circle",
        "geometry_geojson": {"type": "Point", "coordinates": [76.9, 11.0]},
        "center_latitude": 11.0, "center_longitude": 76.9, "radius_m": 500,
        "severity": "MEDIUM", "description": "Water accumulation was reported at a local drain.",
        "evidence_source": "Admin review", "precautions": "Avoid the affected section and follow local authority instructions.",
        "jurisdiction": "Ward API", "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=2)).isoformat(), "publish": True,
    }
    created = client.post("/api/hotspots", headers=admin_headers, json=payload)
    assert created.status_code == 201, created.text
    hotspot_id = created.json()["id"]
    changed = dict(payload, title="Updated drainage advisory", severity="HIGH", radius_m=750)
    edited = client.patch(f"/api/hotspots/{hotspot_id}", headers=admin_headers, json=changed)
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Updated drainage advisory"
    assert edited.json()["severity"] == "HIGH"
    forbidden = client.patch(f"/api/hotspots/{hotspot_id}", headers={"Authorization": f"Bearer {citizen_token}"}, json=changed)
    assert forbidden.status_code == 403

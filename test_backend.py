from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.ai_triage import analyze_image, analyze_text
from backend.civicpulse import compute_signals
from backend.database import (Base, ComplaintMedia, Hotspot, Notification, StatusHistory, User, get_engine,
                              get_session_factory, init_db, iso_now)
from backend.reports import complaints_csv, safe_csv_cell, investigation_pdf
from backend.security import verify_password
from backend.services import (assign_complaint, authenticate_user, create_complaint, create_user,
                               get_complaint, list_hotspots, list_notifications, list_reports_for_user,
                               mark_notification_read, publish_hotspot, submit_investigation_report,
                               transition_complaint, verify_audit_chain)
from backend.storage import validate_upload
from backend.services import uid


@pytest.fixture
def db_factory(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'swasthyagrid-test.db'}"
    init_db(url)
    factory = get_session_factory(url)
    with factory() as session:
        yield session
    Base.metadata.drop_all(get_engine(url))
    get_engine(url).dispose()


def add_user(db, email, role="CITIZEN", jurisdiction="Ward 1", department="General"):
    return create_user(db, email, "LongTestPassword!2026", email.split("@")[0].title(), role, jurisdiction, department)


def new_case(db, citizen, *, severity="MEDIUM", lat=11.0, lon=76.9, category="Sanitation", title="Street litter"):
    return create_complaint(db, citizen, title=title, category=category,
        description="Observed a recurring civic issue that requires inspection.", severity=severity,
        department="Client cannot choose this", jurisdiction=citizen.jurisdiction,
        latitude=lat, longitude=lon, location_status="APPROXIMATE")


def test_all_roles_authenticate_and_passwords_are_hashed(db_factory):
    db = db_factory
    roles = ["CITIZEN", "FIELD_OFFICER", "ADMIN", "REPRESENTATIVE"]
    users = [add_user(db, f"{role.lower()}@example.com", role) for role in roles]
    for user in users:
        logged_in = authenticate_user(db, user.email, "LongTestPassword!2026")
        assert logged_in is not None
        assert logged_in.role == user.role
        assert logged_in.password_hash != "LongTestPassword!2026"
        assert verify_password("LongTestPassword!2026", logged_in.password_hash)
    assert authenticate_user(db, users[0].email, "wrong password") is None


def test_citizen_cannot_read_another_citizens_complaint(db_factory):
    db = db_factory
    citizen_a = add_user(db, "a@example.com")
    citizen_b = add_user(db, "b@example.com")
    case = new_case(db, citizen_a)
    with pytest.raises(PermissionError):
        get_complaint(db, citizen_b, case.id)
    assert get_complaint(db, citizen_a, case.id).id == case.id


def test_critical_case_routes_to_admin_and_creates_receipt(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN")
    citizen = add_user(db, "critical@example.com")
    case = new_case(db, citizen, severity="CRITICAL", category="Water & drainage", title="Overflowing drain")
    alerts = list_notifications(db, admin)
    assert any("Urgent civic report" in n.title and case.tracking_number in n.message for n in alerts)
    citizen_alerts = list_notifications(db, citizen)
    assert any("Complaint registered" == n.title for n in citizen_alerts)
    assert case.department == "Water & Drainage"


def test_admin_hotspot_publish_notifies_matching_citizens_and_closure_persists(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN", "Ward 9")
    citizen = add_user(db, "resident@example.com", "CITIZEN", "Ward 9")
    other = add_user(db, "other@example.com", "CITIZEN", "Ward 10")
    now = datetime.now(timezone.utc)
    hotspot = publish_hotspot(db, admin, title="Drain advisory", category="Water & drainage", geometry_type="Circle",
        geometry_geojson={"type": "Point", "coordinates": [76.9, 11.0]}, center_latitude=11.0, center_longitude=76.9,
        radius_m=500, severity="HIGH", description="Water accumulation observed by an officer.",
        evidence_source="Field inspection", precautions="Avoid contact and report any new overflow to the municipality.",
        jurisdiction="Ward 9", valid_from=now.isoformat(), expires_at=(now + timedelta(days=2)).isoformat(), publish=True)
    assert hotspot.status == "ACTIVE"
    assert any(n.kind == "HOTSPOT" for n in list_notifications(db, citizen))
    assert not any(n.kind == "HOTSPOT" for n in list_notifications(db, other))
    assert any(h.id == hotspot.id for h in list_hotspots(db, citizen))
    from backend.services import set_hotspot_status
    set_hotspot_status(db, admin, hotspot.id, "CLOSED")
    assert not any(h.id == hotspot.id for h in list_hotspots(db, citizen))
    assert any("closed" in n.title.lower() for n in list_notifications(db, citizen))


def test_closed_draft_does_not_notify_citizens(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN", "Ward 1")
    citizen = add_user(db, "citizen@example.com", "CITIZEN", "Ward 1")
    now = datetime.now(timezone.utc)
    hotspot = publish_hotspot(db, admin, title="Draft advisory", category="Sanitation", geometry_type="Point",
        geometry_geojson={"type": "Point", "coordinates": [76.9, 11]}, center_latitude=11, center_longitude=76.9,
        radius_m=200, severity="LOW", description="Draft only description", evidence_source="Report",
        precautions="No precaution published yet.", jurisdiction="Ward 1", valid_from=now.isoformat(),
        expires_at=(now + timedelta(days=3)).isoformat(), publish=False)
    from backend.services import set_hotspot_status
    set_hotspot_status(db, admin, hotspot.id, "CLOSED")
    assert not any(n.kind == "HOTSPOT_UPDATE" for n in list_notifications(db, citizen))


def test_expired_hotspots_are_never_returned_as_active(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN", "Ward 1")
    citizen = add_user(db, "citizen@example.com", "CITIZEN", "Ward 1")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    h = Hotspot(id=uid(), title="Old advisory", category="Sanitation", geometry_type="Circle",
        geometry_geojson=json.dumps({"type": "Point", "coordinates": [76.9, 11]}), center_latitude=11, center_longitude=76.9,
        radius_m=200, severity="LOW", description="Expired", evidence_source="Test", precautions="Avoid area.",
        jurisdiction="Ward 1", status="ACTIVE", valid_from=(past - timedelta(days=1)).isoformat(), expires_at=past.isoformat(),
        created_by=admin.id, created_at=iso_now(), updated_at=iso_now())
    db.add(h); db.commit()
    assert all(row.id != h.id for row in list_hotspots(db, citizen))


def test_geometry_validation_rejects_bad_coordinates_and_polygon(db_factory):
    from backend.services import validate_hotspot_geometry
    with pytest.raises(ValueError):
        validate_hotspot_geometry("Point", {"type": "Point", "coordinates": [200, 11]}, 11, 76)
    with pytest.raises(ValueError):
        validate_hotspot_geometry("Polygon", {"type": "Polygon", "coordinates": [[[76, 11], [76, 11], [76, 11]]]}, 11, 76)
    valid = validate_hotspot_geometry("Polygon", {"type": "Polygon", "coordinates": [[[76.9, 11], [77, 11], [77, 11.1], [76.9, 11.1], [76.9, 11]]]}, 11.05, 76.95)
    assert valid["type"] == "Polygon"


def test_state_transitions_and_proof_of_resolution(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN", "Ward 1")
    citizen = add_user(db, "citizen@example.com", "CITIZEN", "Ward 1")
    officer = add_user(db, "officer@example.com", "FIELD_OFFICER", "Ward 1", "Water & Drainage")
    case = new_case(db, citizen, severity="HIGH", category="Water & drainage")
    assigned = assign_complaint(db, admin, case.id, officer.id)
    assert assigned.status == "ASSIGNED"
    with pytest.raises(ValueError):
        transition_complaint(db, officer, case.id, "CLOSED", "Invalid shortcut")
    transition_complaint(db, officer, case.id, "UNDER_INVESTIGATION", "Inspection started")
    evidence = ComplaintMedia(id=uid(), complaint_id=case.id, uploaded_by=officer.id, stored_name="private/test-after.jpg",
                              original_name="after.jpg", mime_type="image/jpeg", byte_size=123, created_at=iso_now())
    db.add(evidence); db.commit()
    with pytest.raises(ValueError, match="After-action evidence"):
        submit_investigation_report(db, officer, complaint_id=case.id, inspection_at=iso_now(),
            inspection_latitude=11.0, inspection_longitude=76.9, reported_problem=case.description,
            site_observations="A drain grate was blocked with debris.", findings="Obstruction found.", severity="HIGH",
            evidence_refs=[], action_taken="Cleared loose debris.", resources_used="Hand tools", department_referral="",
            pending_work="", pending_reason="", recommended_action="Inspect drain after rainfall.", follow_up_date="",
            resolution_status="RESOLVED", citizen_feedback="", declaration=True, submit=True)
    report = submit_investigation_report(db, officer, complaint_id=case.id, inspection_at=iso_now(),
        inspection_latitude=11.0, inspection_longitude=76.9, reported_problem=case.description,
        site_observations="A drain grate was blocked with debris.", findings="Obstruction found.", severity="HIGH",
        evidence_refs=[f"AFTER:{evidence.id}"], action_taken="Cleared loose debris.", resources_used="Hand tools", department_referral="",
        pending_work="", pending_reason="", recommended_action="Inspect drain after rainfall.", follow_up_date="",
        resolution_status="RESOLVED", citizen_feedback="", declaration=True, submit=True)
    assert report.is_submitted
    transition_complaint(db, officer, case.id, "ACTION_IN_PROGRESS", "Repair follow-up")
    final_case = transition_complaint(db, officer, case.id, "RESOLVED", "Proof verified", "Drain cleared and evidence recorded")
    assert final_case.status == "RESOLVED"
    assert len(list_reports_for_user(db, citizen, case.id)) == 1


def test_civicpulse_clusters_and_persists_evidence(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN", "Ward 1")
    citizen = add_user(db, "citizen@example.com", "CITIZEN", "Ward 1")
    for i in range(3):
        new_case(db, citizen, category="Sanitation", title=f"Waste issue {i}", lat=11 + i * 0.0001, lon=76.9 + i * 0.0001)
    signals = compute_signals(db, days=14, radius_m=1000, threshold=3, persist=True)
    assert signals
    assert signals[0]["report_count"] >= 3
    assert "not a confirmed outbreak" in signals[0]["label"].lower()
    stored = db.query(__import__("backend.database", fromlist=["CivicPulseSignal"]).CivicPulseSignal).all()
    assert stored and json.loads(stored[0].evidence_tracking_json)


def test_notification_read_state_persists_and_audit_chain_is_valid(db_factory):
    db = db_factory
    citizen = add_user(db, "citizen@example.com")
    notifications = list_notifications(db, citizen)
    assert notifications
    chosen = notifications[0]
    mark_notification_read(db, citizen, chosen.id)
    db.expire_all()
    reread = db.query(Notification).filter_by(id=chosen.id).one()
    assert reread.is_read is True
    valid, count = verify_audit_chain(db)
    assert valid and count >= 1


def test_csv_formula_injection_and_pdf_generation(db_factory):
    db = db_factory
    admin = add_user(db, "admin@example.com", "ADMIN")
    citizen = add_user(db, "citizen@example.com")
    case = new_case(db, citizen, title="=HYPERLINK(\"https://example.com\")")
    export = complaints_csv([case]).decode("utf-8-sig")
    assert "'=HYPERLINK" in export
    assert safe_csv_cell("  +SUM(A1:A2)").startswith("'")
    officer = add_user(db, "officer@example.com", "FIELD_OFFICER", "Ward 1")
    assign_complaint(db, admin, case.id, officer.id)
    # Save an ordinary submitted report, then verify a readable PDF is emitted.
    transition_complaint(db, officer, case.id, "UNDER_INVESTIGATION", "Started")
    report = submit_investigation_report(db, officer, complaint_id=case.id, inspection_at=iso_now(),
        inspection_latitude=None, inspection_longitude=None, reported_problem=case.description,
        site_observations="Observed the reported issue at the site.", findings="Issue confirmed.", severity="MEDIUM",
        evidence_refs=[], action_taken="Area cleaned.", resources_used="Manual cleaning kit", department_referral="",
        pending_work="Follow-up visit", pending_reason="Monitor next rainfall", recommended_action="Reinspect within a week.",
        follow_up_date="2026-11-05", resolution_status="IN_PROGRESS", citizen_feedback="", declaration=True, submit=True)
    pdf = investigation_pdf(case, report, officer.full_name)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_ai_fallback_and_upload_validation(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = analyze_image(b"fakebytes", "image/jpeg", "Drain issue", "Water pooled near a drain", "Water & drainage")
    assert result["provider"] == "rules-based-fallback"
    assert result["review_status"] == "NEEDS_HUMAN_REVIEW"
    assert "not a medical diagnosis" in result["safety_notice"].lower()
    result_text = analyze_text("Street litter", "Waste accumulating beside road", "Other")
    assert result_text["suggested_category"] in {"Sanitation", "Other"}
    with pytest.raises(ValueError):
        validate_upload("shell.php", b"<?php echo 1; ?>", "application/x-httpd-php")
    with pytest.raises(ValueError):
        validate_upload("tiny.jpg", b"not-an-image", "image/jpeg")


def test_database_restart_persistence(db_factory, tmp_path):
    db = db_factory
    user = add_user(db, "persist@example.com")
    case = new_case(db, user)
    url = str(db.bind.url)
    db.close()
    init_db(url)
    db2 = get_session_factory(url)()
    assert db2.query(User).filter_by(email="persist@example.com").one().id == user.id
    assert db2.query(__import__("backend.database", fromlist=["Complaint"]).Complaint).filter_by(id=case.id).one().tracking_number == case.tracking_number
    db2.close()


def test_sla_guardian_escalation_records_system_history(db_factory):
    from backend.services import assign_complaint, process_scheduled_tasks
    db = db_factory
    admin = add_user(db, "sla-admin@example.com", "ADMIN", "Ward SLA")
    citizen = add_user(db, "sla-citizen@example.com", "CITIZEN", "Ward SLA")
    officer = add_user(db, "sla-officer@example.com", "FIELD_OFFICER", "Ward SLA", "Water & Drainage")
    case = new_case(db, citizen, severity="CRITICAL", category="Water & drainage")
    assign_complaint(db, admin, case.id, officer.id)
    case.due_at = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    db.commit()
    result = process_scheduled_tasks(db)
    db.refresh(case)
    assert result["sla_escalations"] == 1
    assert case.status == "ESCALATED"
    history = db.query(StatusHistory).filter_by(complaint_id=case.id, actor_id="SYSTEM").first()
    assert history is not None
    assert any(n.kind == "ESCALATION" for n in list_notifications(db, admin))


def test_admin_can_edit_active_hotspot_and_audit_update(db_factory):
    from backend.services import update_hotspot
    db = db_factory
    admin = add_user(db, "hotspot-editor@example.com", "ADMIN", "Ward Edit")
    citizen = add_user(db, "hotspot-resident@example.com", "CITIZEN", "Ward Edit")
    now = datetime.now(timezone.utc)
    hotspot = publish_hotspot(db, admin, title="Original advisory", category="Sanitation", geometry_type="Circle",
        geometry_geojson={"type": "Point", "coordinates": [76.9, 11.0]}, center_latitude=11.0, center_longitude=76.9,
        radius_m=500, severity="MEDIUM", description="Original description.", evidence_source="Inspection",
        precautions="Avoid the marked area and follow municipal instructions.", jurisdiction="Ward Edit",
        valid_from=(now - timedelta(minutes=1)).isoformat(), expires_at=(now + timedelta(days=2)).isoformat(), publish=True)
    edited = update_hotspot(db, admin, hotspot.id, title="Updated advisory", category="Water & drainage",
        geometry_type="Circle", geometry_geojson={"type": "Point", "coordinates": [76.91, 11.02]},
        center_latitude=11.02, center_longitude=76.91, radius_m=750, severity="HIGH",
        description="Updated description after a new inspection.", evidence_source="Follow-up inspection",
        precautions="Keep away from the overflow and report changes to the ward office.", jurisdiction="Ward Edit",
        valid_from=(now - timedelta(minutes=1)).isoformat(), expires_at=(now + timedelta(days=3)).isoformat())
    assert edited.title == "Updated advisory"
    assert edited.severity == "HIGH" and edited.radius_m == 750
    assert edited.center_latitude == pytest.approx(11.02)
    assert any("Advisory updated" in n.title for n in list_notifications(db, citizen))
    valid, count = verify_audit_chain(db)
    assert valid and count >= 2

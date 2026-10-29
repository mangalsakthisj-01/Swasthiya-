from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from shapely.geometry import Point, shape, mapping
from shapely.validation import explain_validity
from sqlalchemy import desc, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import (
    AuditEvent, CaseDependency, Complaint, ComplaintMedia, Hotspot, InvestigationReport,
    InvestigationReportRevision, Notification, SLAPolicy, StatusHistory, User, iso_now,
)
from .security import hash_password, verify_password

ROLES = {"CITIZEN", "FIELD_OFFICER", "ADMIN", "REPRESENTATIVE"}
SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
COMPLAINT_STATUSES = {
    "SUBMITTED", "VALIDATION_PENDING", "NEEDS_REVIEW", "VERIFIED", "ASSIGNED",
    "UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "RESOLVED", "CLOSED", "ESCALATED",
    "REOPENED", "DUPLICATE_LINKED", "REJECTED_WITH_REASON",
}
FINAL_STATUSES = {"CLOSED", "REJECTED_WITH_REASON", "DUPLICATE_LINKED"}

TRANSITIONS: dict[str, set[str]] = {
    "SUBMITTED": {"VALIDATION_PENDING", "NEEDS_REVIEW", "VERIFIED", "ASSIGNED", "REJECTED_WITH_REASON", "DUPLICATE_LINKED", "ESCALATED"},
    "VALIDATION_PENDING": {"NEEDS_REVIEW", "VERIFIED", "REJECTED_WITH_REASON", "ESCALATED"},
    "NEEDS_REVIEW": {"VERIFIED", "ASSIGNED", "REJECTED_WITH_REASON", "DUPLICATE_LINKED", "ESCALATED"},
    "VERIFIED": {"ASSIGNED", "NEEDS_REVIEW", "ESCALATED"},
    "ASSIGNED": {"UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "ESCALATED", "REOPENED"},
    "UNDER_INVESTIGATION": {"ACTION_IN_PROGRESS", "NEEDS_REVIEW", "ESCALATED", "REOPENED"},
    "ACTION_IN_PROGRESS": {"RESOLVED", "ESCALATED", "REOPENED"},
    "RESOLVED": {"CLOSED", "REOPENED", "ACTION_IN_PROGRESS"},
    "CLOSED": {"REOPENED"},
    "ESCALATED": {"ASSIGNED", "UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "RESOLVED", "REOPENED", "CLOSED"},
    "REOPENED": {"ASSIGNED", "UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "ESCALATED"},
    "DUPLICATE_LINKED": set(),
    "REJECTED_WITH_REASON": {"REOPENED"},
}


def uid() -> str:
    return str(uuid.uuid4())


def utc_dt(value: str | None = None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def serialize_model(instance: Any, fields: Iterable[str] | None = None) -> dict[str, Any]:
    names = list(fields) if fields else [c.name for c in instance.__table__.columns]
    result: dict[str, Any] = {}
    for name in names:
        value = getattr(instance, name)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[name] = value
    return result


def public_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "jurisdiction": user.jurisdiction, "department": user.department,
        "is_active": user.is_active, "created_at": user.created_at,
    }


def log_audit(db: Session, actor: User | dict[str, Any] | None, action: str,
              entity_type: str, entity_id: str, details: dict[str, Any] | None = None) -> AuditEvent:
    actor_id = getattr(actor, "id", None) if actor is not None else None
    actor_role = getattr(actor, "role", None) if actor is not None else None
    if isinstance(actor, dict):
        actor_id = actor.get("id")
        actor_role = actor.get("role")
    actor_id = actor_id or "SYSTEM"
    actor_role = actor_role or "SYSTEM"
    if db.bind and db.bind.dialect.name == "postgresql":
        # Serialise chain appends so concurrent API workers cannot create two events with the same predecessor.
        db.execute(text("LOCK TABLE audit_events IN EXCLUSIVE MODE"))
    last = db.query(AuditEvent).order_by(desc(AuditEvent.created_at), desc(AuditEvent.id)).first()
    previous_hash = last.event_hash if last else "GENESIS"
    created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    detail_json = json.dumps(details or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event_material = "|".join([previous_hash, actor_id, actor_role, action, entity_type, entity_id, detail_json, created])
    event_hash = hashlib.sha256(event_material.encode("utf-8")).hexdigest()
    row = AuditEvent(id=uid(), actor_id=str(actor_id), actor_role=str(actor_role), action=action,
                     entity_type=entity_type, entity_id=entity_id, details_json=detail_json,
                     previous_hash=previous_hash, event_hash=event_hash, created_at=created)
    db.add(row)
    db.flush()
    return row


def add_notification(db: Session, user_id: str, title: str, message: str, kind: str,
                     dedupe_key: str, link_entity_type: str = "", link_entity_id: str = "") -> bool:
    existing = db.query(Notification).filter_by(dedupe_key=dedupe_key).first()
    if existing:
        return False
    db.add(Notification(id=uid(), user_id=user_id, title=title, message=message, kind=kind,
                        dedupe_key=dedupe_key, link_entity_type=link_entity_type,
                        link_entity_id=link_entity_id, is_read=False, created_at=iso_now()))
    return True


def create_user(db: Session, email: str, password: str, full_name: str, role: str = "CITIZEN",
                jurisdiction: str = "Unassigned jurisdiction", department: str = "General",
                actor: User | None = None) -> User:
    email = email.strip().lower()
    role = role.strip().upper()
    if role not in ROLES:
        raise ValueError("Unsupported role.")
    if not email or "@" not in email or len(email) > 320:
        raise ValueError("Enter a valid email address.")
    if not full_name.strip() or len(full_name.strip()) > 160:
        raise ValueError("Full name is required (maximum 160 characters).")
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise ValueError("An account with this email already exists.")
    user = User(id=uid(), email=email, password_hash=hash_password(password), full_name=full_name.strip(),
                role=role, jurisdiction=(jurisdiction.strip() or "Unassigned jurisdiction")[:180],
                department=(department.strip() or "General")[:120], is_active=True,
                created_at=iso_now(), updated_at=iso_now())
    db.add(user)
    db.flush()
    log_audit(db, actor or user, "USER_CREATED", "USER", user.id, {"role": role, "jurisdiction": user.jurisdiction})
    if role == "CITIZEN":
        add_notification(db, user.id, "Welcome to SwasthyaGrid",
                         "Your account is ready. You can submit and track civic complaints from this dashboard.",
                         "ACCOUNT", f"welcome:{user.id}")
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None
    log_audit(db, user, "USER_LOGIN", "USER", user.id, {"method": "password"})
    db.commit()
    return user


def require_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise PermissionError("Your role is not authorised to perform this action.")


def can_access_complaint(user: User, complaint: Complaint) -> bool:
    if user.role == "ADMIN":
        return True
    if user.role == "CITIZEN":
        return complaint.citizen_id == user.id
    if user.role == "FIELD_OFFICER":
        return complaint.assigned_officer_id == user.id and complaint.jurisdiction == user.jurisdiction
    if user.role == "REPRESENTATIVE":
        return complaint.jurisdiction == user.jurisdiction
    return False


def list_complaints(db: Session, user: User, status: str | None = None, category: str | None = None,
                    severity: str | None = None, department: str | None = None,
                    created_after: str | None = None, limit: int = 1000, offset: int = 0) -> list[Complaint]:
    query = db.query(Complaint)
    if user.role == "CITIZEN":
        query = query.filter(Complaint.citizen_id == user.id)
    elif user.role == "FIELD_OFFICER":
        query = query.filter(Complaint.assigned_officer_id == user.id, Complaint.jurisdiction == user.jurisdiction)
    elif user.role == "REPRESENTATIVE":
        query = query.filter(Complaint.jurisdiction == user.jurisdiction)
    if status and status != "ALL": query = query.filter(Complaint.status == status)
    if category and category != "ALL": query = query.filter(Complaint.category == category)
    if severity and severity != "ALL": query = query.filter(Complaint.severity == severity)
    if department and department != "ALL": query = query.filter(Complaint.department == department)
    if created_after:
        # The service stores complaint timestamps as normalized ISO strings in UTC.
        query = query.filter(Complaint.created_at >= utc_dt(created_after).isoformat(timespec="seconds"))
    page_size = min(max(int(limit), 1), 1000)
    page_offset = min(max(int(offset), 0), 1000000)
    return query.order_by(desc(Complaint.created_at)).offset(page_offset).limit(page_size).all()


def get_complaint(db: Session, user: User, complaint_id: str) -> Complaint:
    complaint = db.query(Complaint).filter_by(id=complaint_id).first()
    if not complaint:
        raise LookupError("Complaint not found.")
    if not can_access_complaint(user, complaint):
        raise PermissionError("You are not authorised to view this complaint.")
    return complaint


def _notify_staff_for_complaint(db: Session, complaint: Complaint, critical: bool = False) -> None:
    query = db.query(User).filter(User.is_active.is_(True), User.jurisdiction == complaint.jurisdiction,
                                  User.role.in_(["ADMIN", "REPRESENTATIVE"]))
    staff = query.all()
    if critical:
        staff += db.query(User).filter(User.is_active.is_(True), User.role == "ADMIN").all()
    for recipient in {u.id: u for u in staff}.values():
        add_notification(db, recipient.id, "Urgent civic report" if critical else "New civic complaint",
                         f"{complaint.tracking_number}: {complaint.title} ({complaint.severity}).",
                         "URGENT" if critical else "CASE", f"case-created:{complaint.id}:{recipient.id}", "COMPLAINT", complaint.id)


def create_complaint(db: Session, user: User, *, title: str, category: str, description: str,
                     severity: str, department: str, jurisdiction: str, latitude: float | None,
                     longitude: float | None, address_text: str = "", location_status: str = "CONFIRMED",
                     is_demo: bool = False) -> Complaint:
    require_role(user, "CITIZEN")
    title, description = title.strip(), description.strip()
    if not title or len(title) > 180: raise ValueError("Title is required and must be under 180 characters.")
    if not category.strip() or len(category.strip()) > 80: raise ValueError("A valid complaint category is required.")
    if not description or len(description) < 10 or len(description) > 10000: raise ValueError("Description must be 10–10,000 characters.")
    if severity.upper() not in SEVERITIES: raise ValueError("Invalid severity.")
    # Server-side routing is authoritative; clients cannot redirect a report to an unrelated department.
    category_route = {
        "sanitation": "Sanitation / SWM", "waste dumping": "Sanitation / SWM",
        "water & drainage": "Water & Drainage", "vector risk": "Public Health",
        "road & infrastructure": "Roads & Infrastructure", "public facility": "Public Works",
    }
    department = category_route.get(category.strip().lower(), "General")
    if location_status not in {"CONFIRMED", "APPROXIMATE", "UNKNOWN"}: raise ValueError("Invalid location status.")
    if location_status != "UNKNOWN":
        if latitude is None or longitude is None: raise ValueError("Select a map location or explicitly mark the location unknown.")
        if not (-90 <= float(latitude) <= 90 and -180 <= float(longitude) <= 180): raise ValueError("Coordinates are outside valid latitude/longitude ranges.")
    else:
        latitude, longitude = None, None
    policy = db.query(SLAPolicy).filter_by(severity=severity.upper()).first()
    response_hours = policy.response_hours if policy else 24
    now = datetime.now(timezone.utc)
    due_at = (now + timedelta(hours=response_hours)).isoformat(timespec="seconds")
    tracking = f"SG-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    complaint = Complaint(
        id=uid(), tracking_number=tracking, citizen_id=user.id, title=title, category=category.strip()[:80],
        description=description, severity=severity.upper(), status="SUBMITTED", department=department[:120],
        jurisdiction=(jurisdiction.strip() or user.jurisdiction)[:180], latitude=latitude, longitude=longitude,
        location_status=location_status, address_text=address_text.strip()[:300], due_at=due_at,
        resolution_note="", ai_assessment_json="{}", requires_human_review=True, is_demo=is_demo,
        created_at=iso_now(), updated_at=iso_now())
    db.add(complaint)
    db.flush()
    db.add(StatusHistory(id=uid(), complaint_id=complaint.id, actor_id=user.id,
                         previous_status="", new_status="SUBMITTED", remarks="Complaint submitted by citizen.", created_at=iso_now()))
    _notify_staff_for_complaint(db, complaint, severity.upper() == "CRITICAL")
    add_notification(db, user.id, "Complaint registered", f"Your report is registered as {tracking}.",
                     "CASE", f"case-receipt:{complaint.id}:{user.id}", "COMPLAINT", complaint.id)
    log_audit(db, user, "COMPLAINT_CREATED", "COMPLAINT", complaint.id,
              {"tracking_number": tracking, "category": category, "severity": severity.upper(), "location_status": location_status})
    db.commit()
    db.refresh(complaint)
    if db.bind and db.bind.dialect.name == "postgresql" and latitude is not None and longitude is not None:
        db.execute(text("UPDATE complaints SET geom=ST_SetSRID(ST_MakePoint(:lng,:lat),4326) WHERE id=:id"),
                   {"lng": longitude, "lat": latitude, "id": complaint.id})
        db.commit()
    return complaint


def assign_complaint(db: Session, actor: User, complaint_id: str, officer_id: str) -> Complaint:
    require_role(actor, "ADMIN")
    complaint = db.query(Complaint).filter_by(id=complaint_id).first()
    officer = db.query(User).filter_by(id=officer_id, role="FIELD_OFFICER", is_active=True).first()
    if not complaint: raise LookupError("Complaint not found.")
    if not officer: raise ValueError("Choose an active Field Officer.")
    if officer.jurisdiction != complaint.jurisdiction:
        raise ValueError("Officer jurisdiction must match the complaint jurisdiction.")
    old = complaint.status
    if old not in {"SUBMITTED", "VALIDATION_PENDING", "NEEDS_REVIEW", "VERIFIED", "ESCALATED", "REOPENED", "ASSIGNED"}:
        raise ValueError(f"Cannot assign a case while it is {old}.")
    complaint.assigned_officer_id = officer.id
    complaint.status = "ASSIGNED"
    complaint.updated_at = iso_now()
    db.add(StatusHistory(id=uid(), complaint_id=complaint.id, actor_id=actor.id, previous_status=old,
                         new_status="ASSIGNED", remarks=f"Assigned to {officer.full_name}.", created_at=iso_now()))
    add_notification(db, officer.id, "Case assigned", f"{complaint.tracking_number}: {complaint.title}",
                     "ASSIGNMENT", f"assignment:{complaint.id}:{officer.id}", "COMPLAINT", complaint.id)
    add_notification(db, complaint.citizen_id, "Officer assigned", "A field officer has been assigned to your report.",
                     "STATUS", f"assigned-citizen:{complaint.id}:{officer.id}", "COMPLAINT", complaint.id)
    log_audit(db, actor, "COMPLAINT_ASSIGNED", "COMPLAINT", complaint.id, {"officer_id": officer.id})
    db.commit()
    db.refresh(complaint)
    return complaint


def _has_after_evidence(db: Session, complaint_id: str, evidence_refs: list[str]) -> bool:
    evidence_ids = [str(ref).split(":", 1)[1] for ref in evidence_refs if str(ref).startswith("AFTER:") and ":" in str(ref)]
    if not evidence_ids:
        return False
    db.flush()
    return db.query(ComplaintMedia.id).filter(ComplaintMedia.complaint_id == complaint_id,
                                              ComplaintMedia.id.in_(evidence_ids)).first() is not None


def transition_complaint(db: Session, actor: User, complaint_id: str, new_status: str,
                         remarks: str = "", resolution_note: str = "") -> Complaint:
    complaint = db.query(Complaint).filter_by(id=complaint_id).first()
    if not complaint: raise LookupError("Complaint not found.")
    new_status = new_status.upper()
    if new_status not in COMPLAINT_STATUSES: raise ValueError("Unknown workflow state.")
    if not can_access_complaint(actor, complaint): raise PermissionError("You are not authorised for this complaint.")
    old = complaint.status
    if new_status not in TRANSITIONS.get(old, set()): raise ValueError(f"Invalid status transition: {old} → {new_status}.")
    if actor.role == "CITIZEN":
        if new_status != "REOPENED" or old not in {"RESOLVED", "CLOSED", "REJECTED_WITH_REASON"}:
            raise PermissionError("Citizens may only request reopening of their own resolved or closed report.")
    elif actor.role == "FIELD_OFFICER":
        allowed = {"UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "RESOLVED", "ESCALATED", "REOPENED"}
        if new_status not in allowed: raise PermissionError("Field Officers cannot perform this administrative transition.")
        if complaint.assigned_officer_id != actor.id: raise PermissionError("This case is not assigned to you.")
        if new_status == "RESOLVED":
            report = db.query(InvestigationReport).filter_by(complaint_id=complaint.id, officer_id=actor.id, is_submitted=True).order_by(desc(InvestigationReport.updated_at)).first()
            if not report: raise ValueError("Submit an investigation report before marking a case resolved.")
            try:
                evidence_refs = json.loads(report.evidence_refs_json or "[]")
            except Exception:
                evidence_refs = []
            if report.resolution_status.upper() not in {"RESOLVED", "ACTION_COMPLETED"} or not _has_after_evidence(db, complaint.id, evidence_refs):
                raise ValueError("Proof-of-resolution check failed. Submit a report marked resolved with a real after-action evidence attachment first.")
    elif actor.role == "REPRESENTATIVE":
        if new_status != "ESCALATED": raise PermissionError("Representatives may escalate cases but cannot change operational case state.")
    elif actor.role != "ADMIN":
        raise PermissionError("Role cannot change case status.")
    if new_status == "REJECTED_WITH_REASON" and not remarks.strip(): raise ValueError("A rejection reason is required.")
    complaint.status = new_status
    complaint.updated_at = iso_now()
    if resolution_note.strip(): complaint.resolution_note = resolution_note.strip()[:5000]
    db.add(StatusHistory(id=uid(), complaint_id=complaint.id, actor_id=actor.id, previous_status=old,
                         new_status=new_status, remarks=remarks.strip()[:2000], created_at=iso_now()))
    add_notification(db, complaint.citizen_id, f"Report status: {new_status.replace('_', ' ').title()}",
                     remarks.strip() or f"Your report is now {new_status.replace('_', ' ').lower()}.",
                     "STATUS", f"status:{complaint.id}:{new_status}:{complaint.updated_at}", "COMPLAINT", complaint.id)
    if new_status == "REOPENED":
        recipients = db.query(User).filter(User.is_active.is_(True), User.jurisdiction == complaint.jurisdiction,
                                           User.role.in_(["ADMIN", "REPRESENTATIVE"])).all()
        if complaint.assigned_officer_id:
            officer = db.query(User).filter_by(id=complaint.assigned_officer_id, is_active=True).first()
            if officer: recipients.append(officer)
        for recipient in {r.id: r for r in recipients}.values():
            add_notification(db, recipient.id, "Case reopened by citizen",
                             f"{complaint.tracking_number} has been reopened and needs follow-up.", "REOPENED",
                             f"reopened:{complaint.id}:{recipient.id}:{complaint.updated_at}", "COMPLAINT", complaint.id)
    log_audit(db, actor, "COMPLAINT_STATUS_CHANGED", "COMPLAINT", complaint.id,
              {"from": old, "to": new_status, "remarks": remarks.strip()})
    db.commit()
    db.refresh(complaint)
    return complaint


def validate_hotspot_geometry(geometry_type: str, geometry_geojson: dict[str, Any],
                              center_latitude: float, center_longitude: float) -> dict[str, Any]:
    if not (-90 <= float(center_latitude) <= 90 and -180 <= float(center_longitude) <= 180):
        raise ValueError("Hotspot coordinates are outside valid bounds.")
    if geometry_type not in {"Point", "Circle", "Polygon"}: raise ValueError("Geometry must be Point, Circle, or Polygon.")
    geo_type = geometry_geojson.get("type")
    if geo_type == "Feature": geo_type = geometry_geojson.get("geometry", {}).get("type")
    if geometry_type in {"Point", "Circle"}:
        coords = geometry_geojson.get("coordinates", [center_longitude, center_latitude])
        if geometry_geojson.get("type") == "Feature": coords = geometry_geojson.get("geometry", {}).get("coordinates", coords)
        if geo_type != "Point" or len(coords) < 2 or not (-180 <= float(coords[0]) <= 180 and -90 <= float(coords[1]) <= 90):
            raise ValueError("Invalid point geometry.")
        return {"type": "Point", "coordinates": [float(coords[0]), float(coords[1])]}
    try:
        geom = shape(geometry_geojson.get("geometry", geometry_geojson) if geometry_geojson.get("type") == "Feature" else geometry_geojson)
    except Exception as exc:
        raise ValueError("Could not parse polygon boundary.") from exc
    if geom.geom_type != "Polygon" or geom.is_empty or not geom.is_valid:
        raise ValueError(f"Invalid polygon boundary: {explain_validity(geom)}")
    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise ValueError("Polygon contains coordinates outside valid bounds.")
    if geom.area <= 0: raise ValueError("Polygon boundary must have non-zero area.")
    return mapping(geom)


def circle_geojson(latitude: float, longitude: float, radius_m: int, points: int = 48) -> dict[str, Any]:
    if radius_m < 50 or radius_m > 20000: raise ValueError("Hotspot radius must be between 50 m and 20 km.")
    earth = 6371008.8
    coords = []
    lat_r = math.radians(latitude)
    lon_r = math.radians(longitude)
    ang = radius_m / earth
    for i in range(points + 1):
        bearing = 2 * math.pi * i / points
        lat2 = math.asin(math.sin(lat_r) * math.cos(ang) + math.cos(lat_r) * math.sin(ang) * math.cos(bearing))
        lon2 = lon_r + math.atan2(math.sin(bearing) * math.sin(ang) * math.cos(lat_r), math.cos(ang) - math.sin(lat_r) * math.sin(lat2))
        coords.append([math.degrees(lon2), math.degrees(lat2)])
    return {"type": "Polygon", "coordinates": [coords]}


def publish_hotspot(db: Session, actor: User, *, title: str, category: str, geometry_type: str,
                    geometry_geojson: dict[str, Any], center_latitude: float, center_longitude: float,
                    radius_m: int, severity: str, description: str, evidence_source: str,
                    precautions: str, jurisdiction: str, valid_from: str, expires_at: str,
                    publish: bool = False) -> Hotspot:
    require_role(actor, "ADMIN")
    if not title.strip() or not description.strip() or not precautions.strip():
        raise ValueError("Title, description, and precautions are required.")
    severity = severity.upper()
    if severity not in SEVERITIES: raise ValueError("Invalid hotspot severity.")
    geojson = validate_hotspot_geometry(geometry_type, geometry_geojson, center_latitude, center_longitude)
    if geometry_type in {"Point", "Circle"}:
        center_longitude, center_latitude = geojson["coordinates"][:2]
    else:
        polygon = shape(geojson)
        center_longitude, center_latitude = polygon.centroid.x, polygon.centroid.y
    if not jurisdiction.strip():
        raise ValueError("A jurisdiction is required for hotspot targeting.")
    start, end = utc_dt(valid_from), utc_dt(expires_at)
    if end <= start: raise ValueError("Expiry must be later than the valid-from time.")
    now = datetime.now(timezone.utc)
    status = "ACTIVE" if publish and start <= now < end else "DRAFT"
    hotspot = Hotspot(id=uid(), title=title.strip()[:180], category=category[:80], geometry_type=geometry_type,
                      geometry_geojson=json.dumps(geojson, separators=(",", ":")), center_latitude=float(center_latitude),
                      center_longitude=float(center_longitude), radius_m=int(radius_m), severity=severity,
                      description=description.strip(), evidence_source=evidence_source.strip()[:300], precautions=precautions.strip(),
                      jurisdiction=jurisdiction.strip()[:180], status=status, valid_from=start.isoformat(), expires_at=end.isoformat(),
                      created_by=actor.id, reviewed_by=actor.id if status == "ACTIVE" else None,
                      created_at=iso_now(), updated_at=iso_now())
    db.add(hotspot)
    db.flush()
    log_audit(db, actor, "HOTSPOT_CREATED", "HOTSPOT", hotspot.id, {"status": status, "geometry_type": geometry_type})
    if status == "ACTIVE":
        _notify_hotspot_recipients(db, hotspot)
        log_audit(db, actor, "HOTSPOT_PUBLISHED", "HOTSPOT", hotspot.id, {"jurisdiction": hotspot.jurisdiction})
    db.commit()
    db.refresh(hotspot)
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("UPDATE hotspots SET geom=ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326) WHERE id=:id"),
                   {"geometry": hotspot.geometry_geojson, "id": hotspot.id})
        db.commit()
    return hotspot


def update_hotspot(db: Session, actor: User, hotspot_id: str, *, title: str, category: str,
                   geometry_type: str, geometry_geojson: dict[str, Any], center_latitude: float,
                   center_longitude: float, radius_m: int, severity: str, description: str,
                   evidence_source: str, precautions: str, jurisdiction: str,
                   valid_from: str, expires_at: str) -> Hotspot:
    """Edit a draft or active hotspot while preserving an auditable before/after record."""
    require_role(actor, "ADMIN")
    hotspot = db.query(Hotspot).filter_by(id=hotspot_id).first()
    if not hotspot:
        raise LookupError("Hotspot not found.")
    if hotspot.status in {"CLOSED", "EXPIRED"}:
        raise ValueError("Closed or expired advisories are immutable. Create a new advisory to preserve history.")
    if not title.strip() or not category.strip() or not description.strip() or not precautions.strip() or not jurisdiction.strip():
        raise ValueError("Title, category, description, approved precautions and jurisdiction are required.")
    severity = severity.upper()
    if severity not in SEVERITIES:
        raise ValueError("Invalid hotspot severity.")
    if geometry_type not in {"Point", "Circle", "Polygon"}:
        raise ValueError("Geometry must be Point, Circle, or Polygon.")
    if int(radius_m) < 50 or int(radius_m) > 20000:
        raise ValueError("Hotspot radius must be between 50 m and 20 km.")
    geojson = validate_hotspot_geometry(geometry_type, geometry_geojson, center_latitude, center_longitude)
    if geometry_type in {"Point", "Circle"}:
        center_longitude, center_latitude = geojson["coordinates"][:2]
    else:
        polygon = shape(geojson)
        center_longitude, center_latitude = polygon.centroid.x, polygon.centroid.y
    start, end = utc_dt(valid_from), utc_dt(expires_at)
    if end <= start:
        raise ValueError("Expiry must be later than the valid-from time.")
    now = datetime.now(timezone.utc)
    if hotspot.status == "ACTIVE" and not (start <= now < end):
        raise ValueError("An active advisory must remain within its valid-from/expiry window. Close it before moving its window outside the active period.")

    old = {
        "title": hotspot.title, "category": hotspot.category, "geometry_type": hotspot.geometry_type,
        "severity": hotspot.severity, "jurisdiction": hotspot.jurisdiction,
        "valid_from": hotspot.valid_from, "expires_at": hotspot.expires_at,
        "description": hotspot.description, "precautions": hotspot.precautions,
    }
    old_jurisdiction = hotspot.jurisdiction
    hotspot.title = title.strip()[:180]
    hotspot.category = category.strip()[:80]
    hotspot.geometry_type = geometry_type
    hotspot.geometry_geojson = json.dumps(geojson, separators=(",", ":"))
    hotspot.center_latitude = float(center_latitude)
    hotspot.center_longitude = float(center_longitude)
    hotspot.radius_m = int(radius_m)
    hotspot.severity = severity
    hotspot.description = description.strip()
    hotspot.evidence_source = evidence_source.strip()[:300]
    hotspot.precautions = precautions.strip()
    hotspot.jurisdiction = jurisdiction.strip()[:180]
    hotspot.valid_from = start.isoformat()
    hotspot.expires_at = end.isoformat()
    hotspot.reviewed_by = actor.id if hotspot.status == "ACTIVE" else hotspot.reviewed_by
    hotspot.updated_at = iso_now()
    new = {
        "title": hotspot.title, "category": hotspot.category, "geometry_type": hotspot.geometry_type,
        "severity": hotspot.severity, "jurisdiction": hotspot.jurisdiction,
        "valid_from": hotspot.valid_from, "expires_at": hotspot.expires_at,
        "description": hotspot.description, "precautions": hotspot.precautions,
    }
    log_audit(db, actor, "HOTSPOT_UPDATED", "HOTSPOT", hotspot.id, {"before": old, "after": new})
    if hotspot.status == "ACTIVE":
        if old_jurisdiction != hotspot.jurisdiction:
            former_citizens = db.query(User).filter(User.role == "CITIZEN", User.is_active.is_(True),
                                                    User.jurisdiction == old_jurisdiction).all()
            for citizen in former_citizens:
                add_notification(db, citizen.id, f"Advisory coverage updated: {hotspot.title}",
                    f"The advisory coverage changed from {old_jurisdiction} to {hotspot.jurisdiction}. Check the current map and guidance.",
                    "HOTSPOT_UPDATE", f"hotspot:{hotspot.id}:scope-changed:{hotspot.updated_at}:{citizen.id}", "HOTSPOT", hotspot.id)
        recipients = db.query(User).filter(User.is_active.is_(True), User.jurisdiction == hotspot.jurisdiction,
                                            User.role.in_(["CITIZEN", "FIELD_OFFICER", "REPRESENTATIVE", "ADMIN"])).all()
        for recipient in recipients:
            add_notification(db, recipient.id, f"Advisory updated: {hotspot.title}",
                f"A published advisory in {hotspot.jurisdiction} was updated. Severity: {hotspot.severity}. {hotspot.precautions}",
                "HOTSPOT_UPDATE", f"hotspot:{hotspot.id}:updated:{hotspot.updated_at}:{recipient.id}", "HOTSPOT", hotspot.id)
    db.commit()
    db.refresh(hotspot)
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("UPDATE hotspots SET geom=ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326) WHERE id=:id"),
                   {"geometry": hotspot.geometry_geojson, "id": hotspot.id})
        db.commit()
    return hotspot


def _notify_hotspot_recipients(db: Session, hotspot: Hotspot) -> int:
    # Citizens without a precise home coordinate are targeted only by their registered jurisdiction.
    users = db.query(User).filter(User.role == "CITIZEN", User.is_active.is_(True), User.jurisdiction == hotspot.jurisdiction).all()
    sent = 0
    for citizen in users:
        sent += add_notification(db, citizen.id, f"Public advisory: {hotspot.title}",
                                 f"{hotspot.severity} advisory for {hotspot.jurisdiction}. {hotspot.precautions}",
                                 "HOTSPOT", f"hotspot:{hotspot.id}:published:{citizen.id}", "HOTSPOT", hotspot.id)
    # Keep an admin-visible delivery ledger row for affected officers and representatives too.
    staff = db.query(User).filter(User.is_active.is_(True), User.jurisdiction == hotspot.jurisdiction,
                                  User.role.in_(["ADMIN", "FIELD_OFFICER", "REPRESENTATIVE"])).all()
    for recipient in staff:
        sent += add_notification(db, recipient.id, f"Hotspot published: {hotspot.title}",
                                 f"A {hotspot.severity.lower()} advisory is active in {hotspot.jurisdiction} until {hotspot.expires_at}.",
                                 "HOTSPOT", f"hotspot:{hotspot.id}:staff:{recipient.id}", "HOTSPOT", hotspot.id)
    return sent


def list_hotspots(db: Session, user: User, include_inactive: bool = False) -> list[Hotspot]:
    # ISO-8601 strings can sort incorrectly when one value has fractional seconds and
    # another doesn't. Filter validity with parsed, timezone-aware datetimes instead.
    query = db.query(Hotspot)
    if user.role == "CITIZEN":
        query = query.filter(Hotspot.status == "ACTIVE", Hotspot.jurisdiction == user.jurisdiction)
        rows = query.order_by(desc(Hotspot.created_at)).limit(1000).all()
    elif user.role in {"FIELD_OFFICER", "REPRESENTATIVE"}:
        query = query.filter(Hotspot.jurisdiction == user.jurisdiction)
        rows = query.order_by(desc(Hotspot.created_at)).limit(1000).all()
    elif user.role == "ADMIN":
        rows = query.order_by(desc(Hotspot.created_at)).limit(1000).all()
    else:
        rows = []
    if include_inactive and user.role == "ADMIN":
        return rows[:500]
    now = datetime.now(timezone.utc)
    return [h for h in rows if h.status == "ACTIVE" and utc_dt(h.valid_from) <= now < utc_dt(h.expires_at)][:500]


def set_hotspot_status(db: Session, actor: User, hotspot_id: str, new_status: str) -> Hotspot:
    require_role(actor, "ADMIN")
    hotspot = db.query(Hotspot).filter_by(id=hotspot_id).first()
    if not hotspot: raise LookupError("Hotspot not found.")
    new_status = new_status.upper()
    if new_status not in {"ACTIVE", "CLOSED", "EXPIRED"}: raise ValueError("Invalid hotspot status.")
    if hotspot.status in {"CLOSED", "EXPIRED"} and new_status == "ACTIVE":
        raise ValueError("Closed/expired hotspots cannot be reactivated; create a new advisory to preserve history.")
    if new_status == "ACTIVE":
        now = datetime.now(timezone.utc)
        if utc_dt(hotspot.valid_from) > now or utc_dt(hotspot.expires_at) <= now:
            raise ValueError("A hotspot can be published only inside its valid-from and expiry window.")
    old = hotspot.status
    hotspot.status = new_status
    hotspot.updated_at = iso_now()
    hotspot.reviewed_by = actor.id
    if new_status == "CLOSED": hotspot.closed_at = iso_now()
    if new_status == "ACTIVE" and old == "DRAFT":
        _notify_hotspot_recipients(db, hotspot)
        log_audit(db, actor, "HOTSPOT_PUBLISHED", "HOTSPOT", hotspot.id, {"jurisdiction": hotspot.jurisdiction})
    elif new_status in {"CLOSED", "EXPIRED"} and old == "ACTIVE":
        citizens = db.query(User).filter(User.role == "CITIZEN", User.is_active.is_(True), User.jurisdiction == hotspot.jurisdiction).all()
        for citizen in citizens:
            add_notification(db, citizen.id, f"Advisory {new_status.lower()}: {hotspot.title}",
                             f"The advisory '{hotspot.title}' is now {new_status.lower()}.", "HOTSPOT_UPDATE",
                             f"hotspot:{hotspot.id}:{new_status.lower()}:{citizen.id}", "HOTSPOT", hotspot.id)
    log_audit(db, actor, f"HOTSPOT_{new_status}", "HOTSPOT", hotspot.id, {"from": old, "to": new_status})
    db.commit()
    db.refresh(hotspot)
    return hotspot


def process_scheduled_tasks(db: Session) -> dict[str, int]:
    """Idempotent SLA Guardian and expiry worker; safe to run repeatedly."""
    now = datetime.now(timezone.utc)
    counts = {"hotspots_expired": 0, "sla_reminders": 0, "sla_escalations": 0}
    active_hotspots = db.query(Hotspot).filter(Hotspot.status == "ACTIVE").all()
    for hotspot in active_hotspots:
        if utc_dt(hotspot.expires_at) <= now:
            hotspot.status = "EXPIRED"
            hotspot.updated_at = iso_now()
            hotspot.closed_at = iso_now()
            log_audit(db, None, "HOTSPOT_AUTO_EXPIRED", "HOTSPOT", hotspot.id, {})
            citizens = db.query(User).filter(User.role == "CITIZEN", User.is_active.is_(True), User.jurisdiction == hotspot.jurisdiction).all()
            for citizen in citizens:
                add_notification(db, citizen.id, f"Advisory expired: {hotspot.title}",
                                 f"The advisory for {hotspot.jurisdiction} has expired and is no longer active.",
                                 "HOTSPOT_UPDATE", f"hotspot:{hotspot.id}:auto-expired:{citizen.id}", "HOTSPOT", hotspot.id)
            counts["hotspots_expired"] += 1
    policies = {p.severity: p for p in db.query(SLAPolicy).all()}
    for complaint in db.query(Complaint).filter(~Complaint.status.in_(list(FINAL_STATUSES))).all():
        if not complaint.due_at: continue
        overdue_hours = (now - utc_dt(complaint.due_at)).total_seconds() / 3600
        policy = policies.get(complaint.severity)
        escalation_hours = policy.escalation_hours if policy else 48
        recipients = []
        if complaint.assigned_officer_id:
            officer = db.query(User).filter_by(id=complaint.assigned_officer_id).first()
            if officer: recipients.append(officer)
        admins = db.query(User).filter(User.role == "ADMIN", User.is_active.is_(True)).all()
        if overdue_hours >= 0:
            for recipient in recipients:
                if add_notification(db, recipient.id, "SLA reminder", f"{complaint.tracking_number} is overdue.",
                                    "SLA", f"sla-reminder:{complaint.id}:{recipient.id}", "COMPLAINT", complaint.id):
                    counts["sla_reminders"] += 1
        if overdue_hours >= escalation_hours and complaint.status != "ESCALATED":
            old = complaint.status
            complaint.status = "ESCALATED"
            complaint.updated_at = iso_now()
            db.add(StatusHistory(id=uid(), complaint_id=complaint.id, actor_id="SYSTEM", previous_status=old,
                                 new_status="ESCALATED", remarks="Automatic SLA Guardian escalation.", created_at=iso_now()))
            for recipient in admins:
                add_notification(db, recipient.id, "SLA escalation", f"{complaint.tracking_number} exceeded its escalation threshold.",
                                 "ESCALATION", f"sla-escalated:{complaint.id}:{recipient.id}", "COMPLAINT", complaint.id)
            add_notification(db, complaint.citizen_id, "Your report has been escalated",
                             "The service-level deadline was exceeded; the case has been escalated for review.",
                             "STATUS", f"sla-citizen-escalated:{complaint.id}", "COMPLAINT", complaint.id)
            log_audit(db, None, "SLA_AUTO_ESCALATED", "COMPLAINT", complaint.id, {"overdue_hours": round(overdue_hours, 2)})
            counts["sla_escalations"] += 1
    db.commit()
    return counts


def list_notifications(db: Session, user: User, limit: int = 100) -> list[Notification]:
    return db.query(Notification).filter_by(user_id=user.id).order_by(desc(Notification.created_at)).limit(min(max(limit, 1), 200)).all()


def mark_notification_read(db: Session, user: User, notification_id: str) -> None:
    notification = db.query(Notification).filter_by(id=notification_id, user_id=user.id).first()
    if not notification: raise LookupError("Notification not found.")
    notification.is_read = True
    db.commit()


def assign_dependency(db: Session, actor: User, complaint_id: str, target_department: str,
                      description: str, due_at: str = "") -> CaseDependency:
    require_role(actor, "ADMIN", "REPRESENTATIVE")
    complaint = db.query(Complaint).filter_by(id=complaint_id).first()
    if not complaint: raise LookupError("Complaint not found.")
    if actor.role == "REPRESENTATIVE" and complaint.jurisdiction != actor.jurisdiction:
        raise PermissionError("This complaint is outside your jurisdiction.")
    if not target_department.strip() or not description.strip(): raise ValueError("Target department and dependency description are required.")
    dep = CaseDependency(id=uid(), complaint_id=complaint_id, source_department=complaint.department,
                         target_department=target_department.strip()[:120], description=description.strip(),
                         status="PENDING", created_by=actor.id, due_at=due_at, created_at=iso_now())
    db.add(dep)
    log_audit(db, actor, "DEPARTMENT_DEPENDENCY_CREATED", "CASE_DEPENDENCY", dep.id,
              {"complaint_id": complaint_id, "target_department": target_department})
    db.commit()
    db.refresh(dep)
    return dep


def submit_investigation_report(db: Session, officer: User, *, complaint_id: str, inspection_at: str,
                                inspection_latitude: float | None, inspection_longitude: float | None,
                                reported_problem: str, site_observations: str, findings: str, severity: str,
                                evidence_refs: list[str], action_taken: str, resources_used: str,
                                department_referral: str, pending_work: str, pending_reason: str,
                                recommended_action: str, follow_up_date: str, resolution_status: str,
                                citizen_feedback: str, declaration: bool, submit: bool, report_id: str | None = None) -> InvestigationReport:
    require_role(officer, "FIELD_OFFICER", "ADMIN")
    complaint = db.query(Complaint).filter_by(id=complaint_id).first()
    if not complaint: raise LookupError("Complaint not found.")
    if officer.role == "FIELD_OFFICER" and (complaint.assigned_officer_id != officer.id or complaint.jurisdiction != officer.jurisdiction):
        raise PermissionError("You may only investigate cases assigned to you in your jurisdiction.")
    if inspection_latitude is not None or inspection_longitude is not None:
        if inspection_latitude is None or inspection_longitude is None or not (-90 <= inspection_latitude <= 90 and -180 <= inspection_longitude <= 180):
            raise ValueError("Inspection coordinates must be a valid latitude/longitude pair.")
    required = [reported_problem, site_observations, findings, recommended_action]
    if any(not v.strip() for v in required): raise ValueError("Problem, observations, findings and recommended action are mandatory.")
    if submit and not declaration: raise ValueError("Officer declaration is required before submission.")
    if submit and resolution_status.upper() in {"RESOLVED", "ACTION_COMPLETED"} and not _has_after_evidence(db, complaint.id, evidence_refs):
        raise ValueError("After-action evidence is required before a report can claim resolution. Upload the evidence to this case first.")
    if severity.upper() not in SEVERITIES: raise ValueError("Invalid severity.")
    report = db.query(InvestigationReport).filter_by(id=report_id).first() if report_id else None
    is_new = report is None
    if report and report.officer_id != officer.id and officer.role != "ADMIN": raise PermissionError("This report belongs to another officer.")
    snapshot: dict[str, Any] | None = None
    if report:
        snapshot = serialize_model(report)
        report.revision += 1
        report.updated_at = iso_now()
    else:
        report = InvestigationReport(id=uid(), complaint_id=complaint_id, officer_id=officer.id, inspection_at=inspection_at,
            inspection_latitude=inspection_latitude, inspection_longitude=inspection_longitude, reported_problem=reported_problem,
            site_observations=site_observations, findings=findings, severity=severity.upper(), evidence_refs_json="[]",
            action_taken="", resources_used="", department_referral="", pending_work="", pending_reason="",
            recommended_action=recommended_action, follow_up_date=follow_up_date, resolution_status=resolution_status,
            citizen_feedback="", declaration=False, revision=1, is_submitted=False, created_at=iso_now(), updated_at=iso_now())
        db.add(report)
        db.flush()
    report.inspection_at = inspection_at
    report.inspection_latitude = inspection_latitude
    report.inspection_longitude = inspection_longitude
    report.reported_problem = reported_problem.strip()
    report.site_observations = site_observations.strip()
    report.findings = findings.strip()
    report.severity = severity.upper()
    report.evidence_refs_json = json.dumps(evidence_refs)
    report.action_taken = action_taken.strip()
    report.resources_used = resources_used.strip()
    report.department_referral = department_referral.strip()
    report.pending_work = pending_work.strip()
    report.pending_reason = pending_reason.strip()
    report.recommended_action = recommended_action.strip()
    report.follow_up_date = follow_up_date
    report.resolution_status = resolution_status
    report.citizen_feedback = citizen_feedback.strip()
    report.declaration = bool(declaration)
    report.is_submitted = bool(submit)
    report.updated_at = iso_now()
    if snapshot:
        db.add(InvestigationReportRevision(id=uid(), report_id=report.id, revision=report.revision - 1,
                                           actor_id=officer.id, snapshot_json=json.dumps(snapshot, ensure_ascii=False), created_at=iso_now()))
    if submit:
        add_notification(db, complaint.citizen_id, "Investigation report submitted",
                         f"A field investigation report was submitted for {complaint.tracking_number}.",
                         "REPORT", f"investigation-submitted:{report.id}:{report.revision}", "REPORT", report.id)
        log_audit(db, officer, "INVESTIGATION_REPORT_SUBMITTED", "INVESTIGATION_REPORT", report.id,
                  {"complaint_id": complaint.id, "revision": report.revision})
    else:
        log_audit(db, officer, "INVESTIGATION_REPORT_SAVED", "INVESTIGATION_REPORT", report.id,
                  {"complaint_id": complaint.id, "revision": report.revision})
    db.commit()
    db.refresh(report)
    return report


def list_reports_for_user(db: Session, user: User, complaint_id: str | None = None) -> list[InvestigationReport]:
    q = db.query(InvestigationReport)
    if user.role == "FIELD_OFFICER":
        q = q.filter(InvestigationReport.officer_id == user.id)
    elif user.role == "CITIZEN":
        own_ids = [c.id for c in db.query(Complaint.id).filter(Complaint.citizen_id == user.id).all()]
        q = q.filter(InvestigationReport.complaint_id.in_(own_ids or ["none"]))
        q = q.filter(InvestigationReport.is_submitted.is_(True))
    elif user.role == "REPRESENTATIVE":
        complaint_ids = [c.id for c in db.query(Complaint.id).filter(Complaint.jurisdiction == user.jurisdiction).all()]
        q = q.filter(InvestigationReport.complaint_id.in_(complaint_ids or ["none"]))
        q = q.filter(InvestigationReport.is_submitted.is_(True))
    if complaint_id:
        q = q.filter(InvestigationReport.complaint_id == complaint_id)
    return q.order_by(desc(InvestigationReport.created_at)).limit(500).all()


def dashboard_metrics(db: Session, user: User) -> dict[str, int]:
    cases = list_complaints(db, user)
    active = sum(c.status not in FINAL_STATUSES for c in cases)
    urgent = sum(c.severity in {"HIGH", "CRITICAL"} and c.status not in FINAL_STATUSES for c in cases)
    overdue = sum(bool(c.due_at) and utc_dt(c.due_at) < datetime.now(timezone.utc) and c.status not in FINAL_STATUSES for c in cases)
    unread = db.query(Notification).filter_by(user_id=user.id, is_read=False).count()
    return {"total": len(cases), "active": active, "high_risk": urgent, "overdue": overdue, "unread_notifications": unread}


def verify_audit_chain(db: Session) -> tuple[bool, int]:
    events = db.query(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.id).all()
    prev = "GENESIS"
    for row in events:
        material = "|".join([prev, row.actor_id, row.actor_role, row.action, row.entity_type, row.entity_id,
                             row.details_json, row.created_at])
        if row.previous_hash != prev or hashlib.sha256(material.encode("utf-8")).hexdigest() != row.event_hash:
            return False, len(events)
        prev = row.event_hash
    return True, len(events)

"""FastAPI companion API for SwasthyaGrid. All record access is authorised server-side."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

load_dotenv()

from backend.ai_triage import analyze_image, analyze_text
from backend.bootstrap import bootstrap_admin
from backend.civicpulse import compute_signals, review_signal
from backend.database import (AuditEvent, CaseDependency, Complaint, ComplaintMedia, Hotspot, InvestigationReport,
                              Notification, SLAPolicy, StatusHistory, User, db_session, get_db, get_session_factory,
                              init_db, iso_now)
from backend.mailer import send_password_reset_email
from backend.reports import complaints_csv, investigation_pdf
from backend.security import create_access_token, decode_access_token
from backend.security_controls import (clear_login_failures, create_password_reset_token, login_is_blocked,
                                       record_login_failure, reset_password)
from backend.services import (ROLES, assign_complaint, assign_dependency, authenticate_user, can_access_complaint,
                              create_complaint, create_user, dashboard_metrics, get_complaint, list_complaints,
                              list_hotspots, list_notifications, list_reports_for_user, log_audit,
                              mark_notification_read, process_scheduled_tasks, publish_hotspot, public_user,
                              serialize_model, set_hotspot_status, submit_investigation_report, transition_complaint,
                              update_hotspot, verify_audit_chain)
from backend.storage import MAX_FILE_BYTES, read_private_upload, save_private_upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    factory = get_session_factory()
    with factory() as db:
        bootstrap_admin(db)
        if os.getenv("SG_ENABLE_API_STARTUP_TASKS", "false").lower() in {"1", "true", "yes"}:
            process_scheduled_tasks(db)
    yield


app = FastAPI(title="SwasthyaGrid Civic Intelligence API", version="1.0.0",
              description="Role-authorised civic complaint, hotspot, notification and investigation-report APIs.", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


@app.exception_handler(PermissionError)
async def permission_error_handler(request, exc: PermissionError):
    return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": str(exc)}})


@app.exception_handler(LookupError)
async def not_found_handler(request, exc: LookupError):
    return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": str(exc)}})


@app.exception_handler(ValueError)
async def validation_error_handler(request, exc: ValueError):
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}})


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request, exc: RequestValidationError):
    details = [{"field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "message": error.get("msg", "Invalid value"), "type": error.get("type", "validation_error")}
               for error in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "REQUEST_VALIDATION_ERROR",
        "message": "The request contains invalid or missing fields.", "details": details}})


@app.exception_handler(HTTPException)
async def http_error_handler(request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code", "HTTP_ERROR"))
        message = str(detail.get("message", "The request could not be completed."))
        extra = {k: v for k, v in detail.items() if k not in {"code", "message"}}
    else:
        code, message, extra = "HTTP_ERROR", str(detail), {}
    return JSONResponse(status_code=exc.status_code,
        content={"error": {"code": code, "message": message, **extra}}, headers=exc.headers)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "Bearer access token required."})
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail={"code": "INVALID_TOKEN", "message": "Invalid or expired access token."})
    user = db.query(User).filter_by(id=payload.get("sub"), is_active=True).first()
    if not user:
        raise HTTPException(status_code=401, detail={"code": "INACTIVE_USER", "message": "Account is inactive or no longer exists."})
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "This role cannot perform this action."})
        return user
    return checker


def to_safe_complaint(c: Complaint, user: User) -> dict[str, Any]:
    fields = ["id", "tracking_number", "title", "category", "description", "severity", "status", "department",
              "jurisdiction", "latitude", "longitude", "location_status", "due_at", "resolution_note", "created_at", "updated_at"]
    if user.role in {"ADMIN", "CITIZEN", "FIELD_OFFICER"}:
        fields += ["address_text", "citizen_id", "assigned_officer_id", "ai_assessment_json", "requires_human_review"]
    record = serialize_model(c, fields)
    # Representatives need useful ward-level context without precise citizen coordinates.
    if user.role == "REPRESENTATIVE":
        record["latitude"] = round(c.latitude, 2) if c.latitude is not None else None
        record["longitude"] = round(c.longitude, 2) if c.longitude is not None else None
    return record


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    full_name: str = Field(min_length=1, max_length=160)
    jurisdiction: str = Field(default="Coimbatore, Tamil Nadu", max_length=180)

class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

class ComplaintBody(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=10000)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    department: str = Field(default="General", max_length=120)
    jurisdiction: str = Field(default="", max_length=180)
    latitude: float | None = None
    longitude: float | None = None
    location_status: Literal["CONFIRMED", "APPROXIMATE", "UNKNOWN"] = "UNKNOWN"
    address_text: str = Field(default="", max_length=300)

class TransitionBody(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    remarks: str = Field(default="", max_length=2000)
    resolution_note: str = Field(default="", max_length=5000)

class AssignBody(BaseModel):
    officer_id: str

class HotspotBody(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=80)
    geometry_type: Literal["Point", "Circle", "Polygon"]
    geometry_geojson: dict[str, Any]
    center_latitude: float = Field(ge=-90, le=90)
    center_longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=500, ge=50, le=20000)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    description: str = Field(min_length=1, max_length=10000)
    evidence_source: str = Field(default="Administrative assessment", max_length=300)
    precautions: str = Field(min_length=1, max_length=5000)
    jurisdiction: str = Field(min_length=1, max_length=180)
    valid_from: str
    expires_at: str
    publish: bool = False

class StatusBody(BaseModel):
    status: str

class InvestigationBody(BaseModel):
    complaint_id: str
    inspection_at: str
    inspection_latitude: float | None = None
    inspection_longitude: float | None = None
    reported_problem: str
    site_observations: str
    findings: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    evidence_refs: list[str] = Field(default_factory=list)
    action_taken: str = ""
    resources_used: str = ""
    department_referral: str = ""
    pending_work: str = ""
    pending_reason: str = ""
    recommended_action: str
    follow_up_date: str = ""
    resolution_status: str = "IN_PROGRESS"
    citizen_feedback: str = ""
    declaration: bool = False
    submit: bool = False
    report_id: str | None = None

class UserCreateBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    full_name: str = Field(min_length=1, max_length=160)
    role: Literal["CITIZEN", "FIELD_OFFICER", "REPRESENTATIVE", "ADMIN"] = "CITIZEN"
    jurisdiction: str = Field(default="Coimbatore, Tamil Nadu", max_length=180)
    department: str = Field(default="General", max_length=120)

class DependencyBody(BaseModel):
    complaint_id: str
    target_department: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=3000)
    due_at: str = ""

class ResetRequestBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
class ResetConfirmBody(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


@app.get("/health/live")
def health_live():
    return {"status": "ok", "service": "swasthyagrid-api", "time": iso_now()}

@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    db.execute(__import__("sqlalchemy").text("SELECT 1"))
    return {"status": "ready", "database": "connected", "time": iso_now()}

@app.post("/api/auth/register", status_code=201)
def register(body: RegisterBody, db: Session = Depends(get_db)):
    user = create_user(db, body.email, body.password, body.full_name, "CITIZEN", body.jurisdiction, "Resident Services")
    return {"user": public_user(user), "access_token": create_access_token({"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "jurisdiction": user.jurisdiction}), "token_type": "bearer"}

@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    # Do not trust client-supplied X-Forwarded-For for rate limiting. Configure
    # trusted-proxy middleware explicitly if operating behind a known proxy.
    peer = request.client.host if request.client else "unknown"
    identifier = f"{body.email.strip().lower()}|{peer[:80]}"
    blocked, remaining = login_is_blocked(db, identifier)
    if blocked:
        raise HTTPException(status_code=429, detail={"code": "LOGIN_THROTTLED", "message": f"Too many failed attempts. Retry in about {remaining} seconds."})
    user = authenticate_user(db, body.email, body.password)
    if not user:
        record_login_failure(db, identifier)
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect."})
    clear_login_failures(db, identifier)
    token = create_access_token({"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "jurisdiction": user.jurisdiction})
    return {"access_token": token, "token_type": "bearer", "user": public_user(user)}

@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return public_user(user)

@app.post("/api/auth/password-reset/request")
def request_password_reset(body: ResetRequestBody, db: Session = Depends(get_db)):
    # Same response regardless of whether the account exists to avoid account enumeration.
    user = db.query(User).filter(User.email == body.email.strip().lower(), User.is_active.is_(True)).first()
    if user:
        token = create_password_reset_token(db, user)
        try:
            send_password_reset_email(user.email, token)
        except Exception:
            # Do not leak whether the account exists or mail infrastructure failed.
            pass
    return {"message": "If an active account matches this address and email delivery is configured, a reset link will be sent."}

@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(body: ResetConfirmBody, db: Session = Depends(get_db)):
    if not reset_password(db, body.token, body.new_password):
        raise HTTPException(status_code=400, detail={"code": "RESET_TOKEN_INVALID", "message": "The reset link is invalid, expired, or already used."})
    return {"message": "Password updated. Please sign in again."}

@app.get("/api/complaints")
def complaint_list(status: str | None = None, category: str | None = None,
                   severity: str | None = None, department: str | None = None,
                   created_after: str | None = None,
                   limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0, le=1000000),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = list_complaints(db, user, status=status, category=category, severity=severity,
                            department=department, created_after=created_after, limit=limit, offset=offset)
    return [to_safe_complaint(item, user) for item in items]

@app.post("/api/complaints", status_code=201)
def complaint_create(body: ComplaintBody, user: User = Depends(require_roles("CITIZEN")), db: Session = Depends(get_db)):
    complaint = create_complaint(db, user, title=body.title, category=body.category, description=body.description,
                                 severity=body.severity, department=body.department, jurisdiction=body.jurisdiction or user.jurisdiction,
                                 latitude=body.latitude, longitude=body.longitude, address_text=body.address_text,
                                 location_status=body.location_status)
    complaint.ai_assessment_json = json.dumps(analyze_text(complaint.title, complaint.description, complaint.category), ensure_ascii=False)
    db.commit()
    return to_safe_complaint(complaint, user)

@app.get("/api/complaints/{complaint_id}")
def complaint_detail(complaint_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = get_complaint(db, user, complaint_id)
    result = to_safe_complaint(complaint, user)
    result["history"] = [serialize_model(item) for item in db.query(StatusHistory).filter_by(complaint_id=complaint.id).order_by(StatusHistory.created_at).all()]
    result["evidence"] = [serialize_model(item, ["id", "original_name", "mime_type", "byte_size", "created_at"]) for item in db.query(ComplaintMedia).filter_by(complaint_id=complaint.id).all()]
    return result

@app.patch("/api/complaints/{complaint_id}/status")
def complaint_status(complaint_id: str, body: TransitionBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = transition_complaint(db, user, complaint_id, body.status, body.remarks, body.resolution_note)
    return to_safe_complaint(complaint, user)

@app.post("/api/complaints/{complaint_id}/assign")
def complaint_assign(complaint_id: str, body: AssignBody, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return to_safe_complaint(assign_complaint(db, user, complaint_id, body.officer_id), user)

@app.post("/api/complaints/{complaint_id}/evidence", status_code=201)
async def upload_evidence(complaint_id: str, file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = get_complaint(db, user, complaint_id)
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail={"code": "UPLOAD_TOO_LARGE", "message": "File exceeds the configured limit."})
    stored = save_private_upload(file.filename or "evidence", content, file.content_type or "application/octet-stream")
    row = ComplaintMedia(id=__import__("uuid").uuid4().hex, complaint_id=complaint.id, uploaded_by=user.id,
                         stored_name=str(stored["stored_name"]), original_name=str(stored["original_name"]),
                         mime_type=str(stored["mime_type"]), byte_size=int(stored["byte_size"]), created_at=iso_now())
    db.add(row)
    log_audit(db, user, "EVIDENCE_UPLOADED", "COMPLAINT_MEDIA", row.id, {"complaint_id": complaint.id, "size": row.byte_size})
    db.commit()
    return {"id": row.id, "original_name": row.original_name, "mime_type": row.mime_type, "byte_size": row.byte_size, "private": True}

@app.get("/api/complaints/{complaint_id}/evidence/{media_id}/download")
def evidence_download(complaint_id: str, media_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = get_complaint(db, user, complaint_id)
    media = db.query(ComplaintMedia).filter_by(id=media_id, complaint_id=complaint.id).first()
    if not media:
        raise LookupError("Evidence attachment not found for this complaint.")
    content, mime_type = read_private_upload(media.stored_name)
    # Avoid reflecting untrusted names into a raw Content-Disposition header.
    filename = __import__("urllib.parse", fromlist=["quote"]).quote(media.original_name, safe="")
    return Response(content=content, media_type=mime_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Cache-Control": "private, no-store"})


@app.get("/api/hotspots")
def hotspot_list(include_inactive: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if include_inactive and user.role != "ADMIN":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Only administrators can view inactive hotspot records."})
    return [serialize_model(h) for h in list_hotspots(db, user, include_inactive=include_inactive)]

@app.post("/api/hotspots", status_code=201)
def hotspot_create(body: HotspotBody, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    hotspot = publish_hotspot(db, user, title=body.title, category=body.category, geometry_type=body.geometry_type,
        geometry_geojson=body.geometry_geojson, center_latitude=body.center_latitude, center_longitude=body.center_longitude,
        radius_m=body.radius_m, severity=body.severity, description=body.description, evidence_source=body.evidence_source,
        precautions=body.precautions, jurisdiction=body.jurisdiction, valid_from=body.valid_from, expires_at=body.expires_at,
        publish=body.publish)
    return serialize_model(hotspot)

@app.patch("/api/hotspots/{hotspot_id}")
def hotspot_update(hotspot_id: str, body: HotspotBody, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    values = body.model_dump(exclude={"publish"})
    updated = update_hotspot(db, user, hotspot_id, **values)
    return serialize_model(updated)

@app.patch("/api/hotspots/{hotspot_id}/status")
def hotspot_status(hotspot_id: str, body: StatusBody, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return serialize_model(set_hotspot_status(db, user, hotspot_id, body.status))

@app.get("/api/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_model(n) for n in list_notifications(db, user)]

@app.post("/api/notifications/{notification_id}/read")
def notification_read(notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mark_notification_read(db, user, notification_id)
    return {"message": "Notification marked as read."}

@app.get("/api/investigation-reports")
def investigation_reports(complaint_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_model(r) for r in list_reports_for_user(db, user, complaint_id)]

@app.post("/api/investigation-reports", status_code=201)
def investigation_report_create(body: InvestigationBody, user: User = Depends(require_roles("FIELD_OFFICER", "ADMIN")), db: Session = Depends(get_db)):
    return serialize_model(submit_investigation_report(db, user, **body.model_dump()))

@app.get("/api/complaints/export.csv")
def export_complaints(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = complaints_csv(list_complaints(db, user))
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=swasthyagrid-complaints.csv"})

@app.get("/api/complaints/{complaint_id}/investigation.pdf")
def investigation_pdf_export(complaint_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = get_complaint(db, user, complaint_id)
    reports = list_reports_for_user(db, user, complaint_id)
    submitted = [r for r in reports if r.is_submitted]
    if not submitted:
        raise HTTPException(status_code=404, detail={"code": "REPORT_NOT_FOUND", "message": "No submitted investigation report is available for this case."})
    report = sorted(submitted, key=lambda r: (r.updated_at, r.revision))[-1]
    officer = db.query(User).filter_by(id=report.officer_id).first()
    content = investigation_pdf(complaint, report, officer.full_name if officer else "")
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={complaint.tracking_number}-investigation.pdf"})

@app.get("/api/dashboard/metrics")
def metrics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return dashboard_metrics(db, user)

@app.get("/api/civicpulse/signals")
def civicpulse_signals(days: int = Query(default=14, ge=1, le=90), threshold: int = Query(default=3, ge=3, le=20),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CITIZEN":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "CivicPulse signals are restricted to authorised staff."})
    signals = compute_signals(db, days=days, threshold=threshold, persist=user.role == "ADMIN")
    if user.role in {"FIELD_OFFICER", "REPRESENTATIVE"}:
        signals = [s for s in signals if s["jurisdiction"] == user.jurisdiction]
    return signals

@app.patch("/api/civicpulse/signals/{signal_id}/review")
def civicpulse_review(signal_id: str, body: StatusBody, notes: str = "", user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return serialize_model(review_signal(db, user, signal_id, body.status, notes))

@app.post("/api/cases/dependencies", status_code=201)
def case_dependency(body: DependencyBody, user: User = Depends(require_roles("ADMIN", "REPRESENTATIVE")), db: Session = Depends(get_db)):
    result = assign_dependency(db, user, body.complaint_id, body.target_department, body.description, body.due_at)
    return serialize_model(result)

@app.get("/api/admin/users")
def admin_users(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return [public_user(u) for u in db.query(User).order_by(User.created_at.desc()).limit(1000).all()]

@app.post("/api/admin/users", status_code=201)
def admin_create_user(body: UserCreateBody, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return public_user(create_user(db, body.email, body.password, body.full_name, body.role, body.jurisdiction, body.department, actor=user))

@app.patch("/api/admin/users/{user_id}/suspend")
def admin_suspend_user(user_id: str, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "User not found."})
    if target.id == user.id:
        raise HTTPException(status_code=400, detail={"code": "SELF_SUSPEND_BLOCKED", "message": "You cannot suspend your own active session."})
    target.is_active = not target.is_active
    target.updated_at = iso_now()
    log_audit(db, user, "USER_ACTIVE_STATE_CHANGED", "USER", target.id, {"is_active": target.is_active})
    db.commit()
    return public_user(target)

@app.get("/api/admin/sla-policies")
def get_sla_policies(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return [serialize_model(p) for p in db.query(SLAPolicy).order_by(SLAPolicy.severity).all()]

@app.put("/api/admin/sla-policies/{severity}")
def update_sla_policy(severity: str, response_hours: int = Query(ge=1, le=720), escalation_hours: int = Query(ge=1, le=1440),
                      user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    row = db.query(SLAPolicy).filter_by(severity=severity.upper()).first()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "SLA_NOT_FOUND", "message": "Severity policy not found."})
    row.response_hours = response_hours
    row.escalation_hours = escalation_hours
    log_audit(db, user, "SLA_POLICY_UPDATED", "SLA_POLICY", row.id, {"response_hours": response_hours, "escalation_hours": escalation_hours})
    db.commit()
    return serialize_model(row)

@app.get("/api/admin/audit/verify")
def audit_verify(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    ok, count = verify_audit_chain(db)
    return {"chain_valid": ok, "events_checked": count}

@app.get("/api/admin/audit")
def audit_events(limit: int = Query(default=200, ge=1, le=1000), user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return [serialize_model(e) for e in db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).all()]

@app.post("/api/admin/run-scheduled-tasks")
def run_tasks(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    return process_scheduled_tasks(db)

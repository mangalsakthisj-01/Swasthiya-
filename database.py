from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Generator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds")


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/swasthyagrid_app.db").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(180), default="Unassigned jurisdiction", index=True, nullable=False)
    department: Mapped[str] = mapped_column(String(120), default="General", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class Complaint(Base):
    __tablename__ = "complaints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tracking_number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    citizen_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False, default="SUBMITTED")
    department: Mapped[str] = mapped_column(String(120), default="General", nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(180), index=True, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN", nullable=False)
    address_text: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    assigned_officer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    due_at: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ai_assessment_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, index=True, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class ComplaintMedia(Base):
    __tablename__ = "complaint_media"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class StatusHistory(Base):
    __tablename__ = "complaint_status_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(40), nullable=False)
    new_status: Mapped[str] = mapped_column(String(40), nullable=False)
    remarks: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class Hotspot(Base):
    __tablename__ = "hotspots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    geometry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    geometry_geojson: Mapped[str] = mapped_column(Text, nullable=False)
    center_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    center_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(300), default="Administrative assessment", nullable=False)
    precautions: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(180), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True, nullable=False)
    valid_from: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="INFO", nullable=False)
    link_entity_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    link_entity_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(ForeignKey("complaints.id"), index=True, nullable=False)
    officer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    inspection_at: Mapped[str] = mapped_column(String(40), nullable=False)
    inspection_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    inspection_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    reported_problem: Mapped[str] = mapped_column(Text, nullable=False)
    site_observations: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    action_taken: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resources_used: Mapped[str] = mapped_column(Text, default="", nullable=False)
    department_referral: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pending_work: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pending_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    follow_up_date: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(40), nullable=False)
    citizen_feedback: Mapped[str] = mapped_column(Text, default="", nullable=False)
    declaration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), default="GENESIS", nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, index=True, nullable=False)


class SLAPolicy(Base):
    __tablename__ = "sla_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    severity: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    response_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_hours: Mapped[int] = mapped_column(Integer, nullable=False)


class InvestigationReportRevision(Base):
    __tablename__ = "investigation_report_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("investigation_reports.id", ondelete="CASCADE"), index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class CaseDependency(Base):
    __tablename__ = "case_dependencies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True, nullable=False)
    source_department: Mapped[str] = mapped_column(String(120), nullable=False)
    target_department: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    due_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    used_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    identifier_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    blocked_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class CivicPulseSignal(Base):
    __tablename__ = "civicpulse_signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signature: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(180), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    center_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    center_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    report_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_tracking_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[str] = mapped_column(String(40), nullable=False)
    window_end: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED", index=True, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=iso_now, nullable=False)


Index("ix_complaints_geo", Complaint.latitude, Complaint.longitude)
Index("ix_hotspots_validity", Hotspot.status, Hotspot.valid_from, Hotspot.expires_at)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None):
    resolved = url or database_url()
    if resolved in {"sqlite://", "sqlite:///:memory:"}:
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    if resolved.startswith("sqlite:///" ):
        file_part = resolved.replace("sqlite:///", "", 1)
        if file_part != ":memory:":
            Path(file_part).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(resolved, connect_args={"check_same_thread": False}, future=True)
    return create_engine(resolved, pool_pre_ping=True, pool_recycle=1800, future=True)


@lru_cache(maxsize=4)
def get_session_factory(url: str | None = None):
    return sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False, class_=Session)


def init_db(url: str | None = None) -> None:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # Production-only spatial columns/indexes. GeoJSON stays portable for local SQLite development.
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            conn.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS geom geometry(Point,4326)"))
            conn.execute(text("ALTER TABLE hotspots ADD COLUMN IF NOT EXISTS geom geometry(Geometry,4326)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_complaints_geom_gist ON complaints USING GIST (geom)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_hotspots_geom_gist ON hotspots USING GIST (geom)"))
            conn.execute(text("UPDATE complaints SET geom=ST_SetSRID(ST_MakePoint(longitude, latitude),4326) WHERE geom IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"))
            conn.execute(text("UPDATE hotspots SET geom=ST_SetSRID(ST_GeomFromGeoJSON(geometry_geojson),4326) WHERE geom IS NULL AND geometry_geojson IS NOT NULL"))
    SessionFactory = get_session_factory(url)
    with SessionFactory() as session:
        defaults = [("CRITICAL", 2, 4), ("HIGH", 8, 16), ("MEDIUM", 24, 48), ("LOW", 72, 120)]
        for severity, response, escalation in defaults:
            existing = session.query(SLAPolicy).filter_by(severity=severity).first()
            if not existing:
                session.add(SLAPolicy(id=f"sla-{severity.lower()}", severity=severity, response_hours=response, escalation_hours=escalation))
        session.commit()


def get_db(url: str | None = None) -> Generator[Session, None, None]:
    factory = get_session_factory(url)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def db_session(url: str | None = None) -> Session:
    return get_session_factory(url)()

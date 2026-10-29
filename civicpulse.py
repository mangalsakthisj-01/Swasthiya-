"""Transparent, conservative civic report clustering. Signals require human review."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from .database import CivicPulseSignal, Complaint, User, iso_now
from .services import FINAL_STATUSES, log_audit, uid, add_notification


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))


def compute_signals(db: Session, *, days: int = 14, radius_m: int = 1000, threshold: int = 3, persist: bool = True) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, min(days, 90)))
    complaints = db.query(Complaint).filter(
        Complaint.latitude.is_not(None), Complaint.longitude.is_not(None),
        Complaint.status.notin_(list(FINAL_STATUSES)), Complaint.created_at >= cutoff.isoformat()
    ).order_by(Complaint.created_at.desc()).limit(1500).all()
    groups: dict[tuple[str, str], list[Complaint]] = {}
    for c in complaints:
        groups.setdefault((c.jurisdiction, c.category), []).append(c)

    clusters: list[list[Complaint]] = []
    for records in groups.values():
        remaining = {c.id: c for c in records}
        while remaining:
            seed_id, seed = next(iter(remaining.items()))
            stack = [seed]
            cluster: dict[str, Complaint] = {}
            remaining.pop(seed_id, None)
            while stack:
                current = stack.pop()
                cluster[current.id] = current
                nearby_ids = [rid for rid, c in remaining.items()
                              if _distance_m(current.latitude, current.longitude, c.latitude, c.longitude) <= radius_m]
                for rid in nearby_ids:
                    stack.append(remaining.pop(rid))
            if len(cluster) >= threshold:
                clusters.append(list(cluster.values()))

    signals: list[dict[str, Any]] = []
    for cluster in clusters:
        lat = sum(c.latitude for c in cluster) / len(cluster)
        lon = sum(c.longitude for c in cluster) / len(cluster)
        jurisdiction = cluster[0].jurisdiction
        category = cluster[0].category
        signature_seed = f"{jurisdiction}|{category}|{round(lat, 3)}|{round(lon, 3)}|{cutoff.date().isoformat()}"
        signature = hashlib.sha256(signature_seed.encode("utf-8")).hexdigest()
        evidence = [{"id": c.id, "tracking_number": c.tracking_number, "created_at": c.created_at,
                     "severity": c.severity, "status": c.status} for c in sorted(cluster, key=lambda x: x.created_at)]
        severity = "HIGH" if len(cluster) >= threshold + 3 or any(c.severity == "CRITICAL" for c in cluster) else "MEDIUM"
        row = db.query(CivicPulseSignal).filter_by(signature=signature).first()
        if persist and not row:
            row = CivicPulseSignal(
                id=uid(), signature=signature, jurisdiction=jurisdiction, category=category, severity=severity,
                center_latitude=lat, center_longitude=lon, radius_m=radius_m,
                report_ids_json=json.dumps([c.id for c in cluster]), evidence_tracking_json=json.dumps(evidence),
                report_count=len(cluster), window_start=cutoff.isoformat(), window_end=now.isoformat(),
                status="REVIEW_REQUIRED", created_at=iso_now(), updated_at=iso_now(), review_notes="",
            )
            db.add(row)
            db.flush()
            admins = db.query(User).filter(User.role == "ADMIN", User.is_active.is_(True)).all()
            for admin in admins:
                add_notification(db, admin.id, "CivicPulse signal needs review",
                                 f"{len(cluster)} reports in {jurisdiction} may indicate a repeat civic issue ({category}). Review evidence before acting.",
                                 "CIVICPULSE", f"civicpulse:{signature}:{admin.id}", "CIVICPULSE_SIGNAL", row.id)
            log_audit(db, None, "CIVICPULSE_SIGNAL_CREATED", "CIVICPULSE_SIGNAL", row.id,
                      {"category": category, "jurisdiction": jurisdiction, "report_count": len(cluster), "evidence_ids": [c.id for c in cluster]})
        elif row:
            row.report_count = max(row.report_count, len(cluster))
            row.evidence_tracking_json = json.dumps(evidence)
            row.report_ids_json = json.dumps([c.id for c in cluster])
            row.window_end = now.isoformat()
            row.updated_at = iso_now()
        signal = {
            "id": row.id if row else "preview-" + signature[:12], "signature": signature,
            "jurisdiction": jurisdiction, "category": category, "severity": severity,
            "center_latitude": lat, "center_longitude": lon, "radius_m": radius_m,
            "report_count": len(cluster), "evidence": evidence,
            "status": row.status if row else "PREVIEW_ONLY_REVIEW_REQUIRED",
            "label": "Candidate signal — not a confirmed outbreak",
        }
        signals.append(signal)
    if persist:
        db.commit()
    signals.sort(key=lambda item: (item["severity"] == "HIGH", item["report_count"]), reverse=True)
    return signals


def review_signal(db: Session, actor: User, signal_id: str, status: str, notes: str = "") -> CivicPulseSignal:
    if actor.role != "ADMIN":
        raise PermissionError("Only administrators may review CivicPulse signals.")
    signal = db.query(CivicPulseSignal).filter_by(id=signal_id).first()
    if not signal:
        raise LookupError("CivicPulse signal not found.")
    status = status.upper()
    if status not in {"ACKNOWLEDGED", "DISMISSED", "ACTION_CREATED"}:
        raise ValueError("Review status must be ACKNOWLEDGED, DISMISSED, or ACTION_CREATED.")
    signal.status = status
    signal.reviewed_by = actor.id
    signal.reviewed_at = iso_now()
    signal.review_notes = notes.strip()[:3000]
    signal.updated_at = iso_now()
    log_audit(db, actor, "CIVICPULSE_SIGNAL_REVIEWED", "CIVICPULSE_SIGNAL", signal.id,
              {"status": status, "notes": signal.review_notes})
    db.commit()
    db.refresh(signal)
    return signal

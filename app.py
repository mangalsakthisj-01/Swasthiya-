from __future__ import annotations

import csv
import html
import io
import json
import os
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

import folium
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="SwasthyaGrid | Civic Intelligence", page_icon="🧭", layout="wide", initial_sidebar_state="expanded")
from folium.plugins import Draw, HeatMap, MarkerCluster
from reportlab.lib.pagesizes import A4
from shapely.geometry import shape
from streamlit_folium import st_folium
from sqlalchemy.orm import Session


def _load_streamlit_secrets() -> None:
    """Expose Streamlit Community Cloud secrets as environment variables for shared services."""
    keys = [
        "DATABASE_URL", "SG_JWT_SECRET", "SG_BOOTSTRAP_ADMIN_EMAIL", "SG_BOOTSTRAP_ADMIN_PASSWORD",
        "SG_BOOTSTRAP_ADMIN_NAME", "SG_BOOTSTRAP_ADMIN_JURISDICTION", "GEMINI_API_KEY", "SG_GEMINI_MODEL",
        "SG_PRIVATE_STORAGE_DIR", "SG_S3_BUCKET", "SG_S3_ENDPOINT_URL", "SG_S3_REGION", "SG_S3_PREFIX",
        "SG_S3_SERVER_SIDE_ENCRYPTION", "SG_MAX_UPLOAD_BYTES", "SG_SMTP_HOST", "SG_SMTP_PORT",
        "SG_SMTP_USERNAME", "SG_SMTP_PASSWORD", "SG_SMTP_FROM", "SG_PUBLIC_APP_URL", "SG_CONTACT_EMAIL", "APP_ENV",
        "SG_CIVICPULSE_WINDOW_DAYS", "SG_CIVICPULSE_RADIUS_M", "SG_CIVICPULSE_THRESHOLD",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    ]
    try:
        for key in keys:
            value = st.secrets.get(key, None)
            if value is not None and str(value).strip():
                os.environ[key] = str(value)
    except Exception:
        # Local .env or shell variables remain supported when Streamlit has no secrets file.
        pass


_load_streamlit_secrets()

from backend.ai_triage import analyze_image, analyze_text
from backend.bootstrap import bootstrap_admin
from backend.civicpulse import compute_signals, review_signal
from backend.database import (AuditEvent, CaseDependency, CivicPulseSignal, Complaint, ComplaintMedia, Hotspot,
                              InvestigationReport, Notification, SLAPolicy, StatusHistory, User,
                              get_session_factory, init_db, iso_now)
from backend.mailer import send_password_reset_email
from backend.reports import complaints_csv, investigation_pdf, safe_csv_cell
from backend.security_controls import create_password_reset_token, reset_password
from backend.services import (COMPLAINT_STATUSES, FINAL_STATUSES, TRANSITIONS, assign_complaint, assign_dependency,
                              authenticate_user, can_access_complaint, create_complaint, create_user,
                              dashboard_metrics, get_complaint, list_complaints, list_hotspots,
                              list_notifications, list_reports_for_user, log_audit, mark_notification_read,
                              process_scheduled_tasks, publish_hotspot, public_user, serialize_model,
                              set_hotspot_status, submit_investigation_report, transition_complaint,
                              update_hotspot, verify_audit_chain, uid)
from backend.storage import MAX_FILE_BYTES, read_private_upload, save_private_upload

st.markdown("""
<style>
:root { --sg-ink:#10233f; --sg-navy:#0b1730; --sg-teal:#0ea5a4; --sg-mist:#eef4f7; }
[data-testid="stAppViewContainer"] { background: linear-gradient(180deg,#f7fafc 0%,#edf3f7 100%); }
[data-testid="stHeader"] { background: rgba(247,250,252,.88); }
[data-testid="stSidebar"] { background: linear-gradient(180deg,#0b1730 0%,#10233f 100%); }
[data-testid="stSidebar"] * { color: #e8f1fb !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding: .35rem .45rem; border-radius: .55rem; }
.sg-hero { background: linear-gradient(125deg,#0b1730 0%,#133d59 68%,#0b797d 100%); padding: 1.5rem 1.7rem; border-radius: 1.15rem; color: white; margin: .25rem 0 1rem; box-shadow: 0 12px 34px rgba(10,35,62,.12); }
.sg-kicker { text-transform: uppercase; letter-spacing: .13em; font-size:.72rem; color:#94dede; font-weight:800; }
.sg-hero h1 { color:white; margin:.35rem 0 .45rem; font-size:2.0rem; line-height:1.15; }
.sg-hero p { color:#dbe9f5; max-width:950px; margin:0; }
.sg-section { color:#10233f; font-weight:800; font-size:1.1rem; margin:.25rem 0 .65rem; }
.sg-note { background:#e8f6f5; border-left:4px solid #0ea5a4; padding:.75rem .9rem; border-radius:.4rem; color:#173f4b; }
.sg-warning { background:#fff7e8; border-left:4px solid #e9a23b; padding:.75rem .9rem; border-radius:.4rem; color:#6d4b16; }
div[data-testid="stMetric"] { background:white; padding:.8rem .9rem; border:1px solid #e1eaf0; border-radius:.8rem; box-shadow:0 3px 12px rgba(16,35,63,.035); }
.stButton button, [data-testid="stFormSubmitButton"] button { border-radius:.65rem; font-weight:700; }
[data-testid="stDataFrame"] { border-radius:.7rem; overflow:hidden; }
hr { border-color:#dbe5ed; }
footer { visibility:hidden; }
</style>
""", unsafe_allow_html=True)

APP_VERSION = "1.0.0"
DEFAULT_CENTER = (11.0168, 76.9558)  # Coimbatore city centre, only a map starting point—not a complaint location.
CATEGORIES = ["Sanitation", "Water & drainage", "Vector risk", "Waste dumping", "Road & infrastructure", "Public facility", "Other"]
SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
DEPARTMENTS = ["Public Health", "Sanitation / SWM", "Water & Drainage", "Roads & Infrastructure", "Public Works", "Electricity", "General"]
ROLE_LABELS = {"CITIZEN": "Citizen", "FIELD_OFFICER": "Field Officer", "ADMIN": "Administrator", "REPRESENTATIVE": "Representative"}


def init_runtime() -> None:
    if st.session_state.get("sg_runtime_ready"):
        return
    init_db()
    factory = get_session_factory()
    with factory() as db:
        msg = bootstrap_admin(db)
        if msg:
            st.session_state["sg_bootstrap_message"] = msg
    st.session_state["sg_runtime_ready"] = True


def get_db() -> Session:
    return get_session_factory()()


def current_user(db: Session) -> User | None:
    user_id = st.session_state.get("sg_user_id")
    if not user_id:
        return None
    user = db.query(User).filter_by(id=user_id, is_active=True).first()
    if not user:
        st.session_state.pop("sg_user_id", None)
        st.session_state.pop("sg_user_name", None)
        return None
    return user


def hero(title: str, subtitle: str, eyebrow: str = "Civic intelligence · Public health") -> None:
    st.markdown(f'<div class="sg-hero"><div class="sg-kicker">{html.escape(eyebrow)}</div><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>', unsafe_allow_html=True)


def utc_iso(day: date, clock: dt_time) -> str:
    return datetime.combine(day, clock, tzinfo=timezone.utc).isoformat(timespec="seconds")


def _safe_coords(complaint: Complaint, user: User) -> tuple[float, float] | None:
    if complaint.latitude is None or complaint.longitude is None:
        return None
    lat, lon = complaint.latitude, complaint.longitude
    if user.role == "REPRESENTATIVE":
        # A constituency dashboard needs neighbourhood-scale patterns, not doorstep precision.
        return round(lat, 2), round(lon, 2)
    return lat, lon


@st.cache_data(ttl=600, show_spinner=False)
def geocode_address(query: str) -> dict[str, Any] | None:
    if len(query.strip()) < 3:
        return None
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query.strip(), "format": "jsonv2", "limit": 1, "addressdetails": 1},
        headers={"User-Agent": f"SwasthyaGrid/1.0 ({os.getenv('SG_CONTACT_EMAIL', 'operator contact not configured')})"},
        timeout=(4, 8),
    )
    response.raise_for_status()
    items = response.json()
    if not items:
        return None
    item = items[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": str(item.get("display_name", query))[:300]}


def display_map(db: Session, user: User, *, key: str, selectable: bool = False, draw_polygon: bool = False,
                show_heatmap: bool = True, height: int = 560, category_filter: str = "ALL",
                status_filter: str = "ALL", severity_filter: str = "ALL", department_filter: str = "ALL",
                period_days: int | None = None) -> dict[str, Any]:
    created_after = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat(timespec="seconds") if period_days else None
    complaints = list_complaints(db, user,
        category=None if category_filter == "ALL" else category_filter,
        status=None if status_filter == "ALL" else status_filter,
        severity=None if severity_filter == "ALL" else severity_filter,
        department=None if department_filter == "ALL" else department_filter,
        created_after=created_after, limit=1000)
    hotspots = list_hotspots(db, user, include_inactive=(user.role == "ADMIN" and key.startswith("admin")))
    selected = st.session_state.get(f"{key}_selected_point")
    coords = [_safe_coords(c, user) for c in complaints]
    known_coords = [c for c in coords if c]
    center = selected or (known_coords[0] if known_coords else DEFAULT_CENTER)
    fmap = folium.Map(location=center, zoom_start=12, control_scale=True, tiles=None, prefer_canvas=True)
    folium.TileLayer("OpenStreetMap", name="Street map", attr="© OpenStreetMap contributors", control=True).add_to(fmap)
    markers = MarkerCluster(name="Authorised reports", disableClusteringAtZoom=15)
    for c in complaints:
        point = _safe_coords(c, user)
        if not point:
            continue
        popup_text = "<br>".join([
            f"<b>{html.escape(c.tracking_number)}</b>", html.escape(c.title),
            f"Status: {html.escape(c.status.replace('_', ' ').title())}",
            f"Severity: {html.escape(c.severity)}", f"Category: {html.escape(c.category)}",
        ])
        color = {"CRITICAL": "red", "HIGH": "orange", "MEDIUM": "blue", "LOW": "green"}.get(c.severity, "blue")
        folium.Marker(point, popup=folium.Popup(popup_text, max_width=310), tooltip=f"{c.severity}: {c.tracking_number}",
                      icon=folium.Icon(color=color, icon="exclamation-sign", prefix="glyphicon")).add_to(markers)
    markers.add_to(fmap)
    for h in hotspots:
        popup_text = f"<b>{html.escape(h.title)}</b><br>{html.escape(h.severity)} · {html.escape(h.category)}<br>{html.escape(h.description[:350])}<br>Expires: {html.escape(h.expires_at)}"
        if h.geometry_type == "Circle":
            folium.Circle(location=(h.center_latitude, h.center_longitude), radius=h.radius_m,
                          color="#e35d6a", fill=True, fill_opacity=.16, weight=2,
                          tooltip=f"Hotspot · {h.severity}", popup=folium.Popup(popup_text, max_width=320)).add_to(fmap)
        elif h.geometry_type == "Polygon":
            try:
                geom = json.loads(h.geometry_geojson)
                folium.GeoJson(geom, name=f"Hotspot: {h.title}", style_function=lambda _: {"color": "#e35d6a", "fillColor": "#e35d6a", "fillOpacity": .14, "weight": 2},
                               tooltip=f"Hotspot · {h.severity}", popup=folium.Popup(popup_text, max_width=320)).add_to(fmap)
            except Exception:
                pass
        else:
            folium.Marker((h.center_latitude, h.center_longitude), popup=folium.Popup(popup_text, max_width=320),
                          tooltip=f"Hotspot · {h.severity}", icon=folium.Icon(color="red", icon="warning-sign", prefix="glyphicon")).add_to(fmap)
    if show_heatmap and len(known_coords) >= 3 and user.role != "CITIZEN":
        heat = [[round(lat, 2), round(lon, 2), 0.7] for lat, lon in known_coords]
        HeatMap(heat, name="Aggregated report density", radius=18, blur=24, min_opacity=.25).add_to(fmap)
    if selectable:
        folium.Marker(location=center, tooltip="Selected location", icon=folium.Icon(color="cadetblue", icon="map-marker", prefix="glyphicon")).add_to(fmap) if selected else None
    if draw_polygon:
        Draw(export=False, position="topleft", draw_options={"polyline": False, "marker": False, "circle": False,
             "circlemarker": False, "rectangle": True, "polygon": {"allowIntersection": False}},
             edit_options={"edit": True, "remove": True}).add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)
    result = st_folium(fmap, height=height, use_container_width=True, key=f"map_{key}",
                       returned_objects=["last_clicked", "all_drawings"] if (selectable or draw_polygon) else [])
    if selectable and result and result.get("last_clicked"):
        clicked = result["last_clicked"]
        try:
            point = (float(clicked["lat"]), float(clicked["lng"]))
            if -90 <= point[0] <= 90 and -180 <= point[1] <= 180:
                st.session_state[f"{key}_selected_point"] = point
        except (KeyError, TypeError, ValueError):
            pass
    if draw_polygon and result and result.get("all_drawings"):
        drawings = result["all_drawings"]
        if isinstance(drawings, dict):
            features = drawings.get("features", []) if drawings.get("type") == "FeatureCollection" else [drawings]
        elif isinstance(drawings, list):
            features = drawings
        else:
            features = []
        if features:
            candidate = features[-1]
            geometry = candidate.get("geometry") if isinstance(candidate, dict) else None
            if geometry and geometry.get("type") == "Polygon":
                st.session_state[f"{key}_drawn_geojson"] = geometry
    return result or {}


def render_notifications(db: Session, user: User) -> None:
    hero("Notifications", "Persistent in-app alerts for complaints, hotpots, assignments and service deadlines.")
    rows = list_notifications(db, user, 200)
    if not rows:
        st.info("You're all caught up. New case and hotspot alerts will appear here.")
        return
    for n in rows:
        with st.container(border=True):
            cols = st.columns([.12, .68, .20])
            cols[0].markdown("🔔" if not n.is_read else "✓")
            cols[1].markdown(f"**{n.title}**  ")
            cols[1].caption(f"{n.created_at} · {n.kind}")
            cols[1].write(n.message)
            if not n.is_read and cols[2].button("Mark read", key=f"read_{n.id}"):
                mark_notification_read(db, user, n.id)
                st.rerun()


def render_overview(db: Session, user: User) -> None:
    names = {
        "CITIZEN": (f"Welcome, {user.full_name.split()[0]}", "Report local concerns, follow progress and receive verified municipal advisories."),
        "FIELD_OFFICER": (f"Welcome, {user.full_name.split()[0]}", "Manage assigned cases, document inspections and close the loop with evidence."),
        "ADMIN": (f"Welcome, {user.full_name.split()[0]}", "Operate the jurisdiction-wide civic response system, hotspots, roles and service levels."),
        "REPRESENTATIVE": (f"Welcome, {user.full_name.split()[0]}", "Monitor constituency-level trends, escalations and cross-department follow-ups."),
    }
    title, subtitle = names.get(user.role, ("Welcome", "Civic intelligence workspace"))
    hero(title, subtitle, f"{ROLE_LABELS[user.role]} workspace · {user.jurisdiction}")
    metrics = dashboard_metrics(db, user)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Visible cases", metrics["total"])
    c2.metric("Active", metrics["active"])
    c3.metric("High / critical", metrics["high_risk"])
    c4.metric("Overdue", metrics["overdue"])
    c5.metric("Unread alerts", metrics["unread_notifications"])
    cases = list_complaints(db, user)
    left, right = st.columns([1.35, 1])
    with left:
        st.subheader("Case status overview")
        if cases:
            counts = pd.DataFrame([{"Status": c.status.replace("_", " ").title()} for c in cases]).value_counts().rename("Cases").reset_index()
            st.bar_chart(counts.set_index("Status"))
        else:
            st.info("No cases are visible for this account yet.")
    with right:
        st.subheader("Priority queue")
        high = [c for c in cases if c.severity in {"HIGH", "CRITICAL"} and c.status not in FINAL_STATUSES]
        if high:
            st.dataframe(pd.DataFrame([{"Case": c.tracking_number, "Title": c.title, "Severity": c.severity,
                                        "Status": c.status.replace("_", " ").title(), "Due": c.due_at or "—"} for c in high[:8]]),
                         hide_index=True, use_container_width=True)
        else:
            st.success("No active high-severity items in your authorised queue.")
    st.markdown('<div class="sg-note">Privacy first: exact report locations are shown only to authorised staff and to the citizen who filed the report. Representative maps use neighbourhood-level coordinates.</div>', unsafe_allow_html=True)


def render_map_page(db: Session, user: User, *, selection_key: str, selectable: bool = False, draw_polygon: bool = False) -> None:
    hero("Live civic map", "Explore real street tiles, authorised reports and active public advisories. Click the map to set a location.")
    f1, f2, f3, f4, f5 = st.columns([1, 1, 1, 1, 1])
    with f1:
        cat = st.selectbox("Category", ["ALL"] + CATEGORIES, key=f"filter_cat_{selection_key}")
    with f2:
        status = st.selectbox("Status", ["ALL"] + sorted(COMPLAINT_STATUSES), key=f"filter_status_{selection_key}")
    with f3:
        severity = st.selectbox("Severity", ["ALL"] + SEVERITIES, key=f"filter_sev_{selection_key}")
    with f4:
        department = st.selectbox("Department", ["ALL"] + DEPARTMENTS, key=f"filter_dept_{selection_key}")
    with f5:
        period = st.selectbox("Period", ["All time", "Last 7 days", "Last 30 days", "Last 90 days"], key=f"filter_period_{selection_key}")
    period_days = {"All time": None, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}[period]
    created_after = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat(timespec="seconds") if period_days else None
    cases = list_complaints(db, user, status=None if status == "ALL" else status,
                            category=None if cat == "ALL" else cat, severity=None if severity == "ALL" else severity,
                            department=None if department == "ALL" else department, created_after=created_after)
    # display_map applies the same server-side role scope; filters are also reflected in the accessible list.
    display_map(db, user, key=selection_key, selectable=selectable, draw_polygon=draw_polygon, show_heatmap=True,
                category_filter=cat, status_filter=status, severity_filter=severity,
                department_filter=department, period_days=period_days)
    if selectable:
        point = st.session_state.get(f"{selection_key}_selected_point")
        if point:
            st.caption(f"Selected location: {point[0]:.6f}, {point[1]:.6f}")
        else:
            st.caption("No location selected yet. Click the map to set an approximate point, or use address search in the complaint form.")
    st.subheader("Accessible incident list")
    if cases:
        st.dataframe(pd.DataFrame([{"Case ID": c.tracking_number, "Title": c.title, "Category": c.category,
                                    "Severity": c.severity, "Status": c.status, "Department": c.department,
                                    "Created": c.created_at, "Location": f"{c.latitude:.4f}, {c.longitude:.4f}" if c.latitude is not None and user.role != "REPRESENTATIVE" else ("Approximate" if c.latitude is not None else "Unknown")}
                                   for c in cases]), hide_index=True, use_container_width=True)
    else:
        st.info("No cases match these filters. The map remains usable for selecting a location.")


def _save_media_for_complaint(db: Session, complaint: Complaint, user: User, file_obj: Any, *, label: str = "") -> str:
    content = file_obj.getvalue() if hasattr(file_obj, "getvalue") else bytes(file_obj)
    filename = f"{label}_{file_obj.name}" if label else file_obj.name
    stored = save_private_upload(filename, content, getattr(file_obj, "type", None))
    row = ComplaintMedia(id=uid(), complaint_id=complaint.id, uploaded_by=user.id,
                         stored_name=str(stored["stored_name"]), original_name=str(stored["original_name"]),
                         mime_type=str(stored["mime_type"]), byte_size=int(stored["byte_size"]), created_at=iso_now())
    db.add(row)
    return row.id


def render_new_complaint(db: Session, user: User) -> None:
    hero("Report a civic issue", "Give officials enough context to triage the issue. Critical reports are routed immediately; AI review never blocks submission.")
    st.markdown("<div class='sg-warning'>Share only information needed to investigate the issue. Do not upload faces, identity documents, or unrelated personal information.</div>", unsafe_allow_html=True)
    with st.expander("Find a location by address", expanded=False):
        search_cols = st.columns([.75, .25])
        address_query = search_cols[0].text_input("Street, landmark or neighbourhood", key="geocode_query")
        if search_cols[1].button("Search address", use_container_width=True):
            try:
                found = geocode_address(address_query)
                if found:
                    st.session_state["complaint_selected_point"] = (found["lat"], found["lon"])
                    st.session_state["complaint_selected_address"] = found["display_name"]
                    st.success(f"Location selected: {found['display_name']}")
                else:
                    st.warning("No result found. Try a more specific landmark or click the map.")
            except Exception as exc:
                st.warning(f"Geocoding is temporarily unavailable ({type(exc).__name__}). Click the map instead.")
    display_map(db, user, key="complaint", selectable=True, show_heatmap=False, height=420)
    selected = st.session_state.get("complaint_selected_point")
    if selected:
        cols = st.columns([.7, .3])
        cols[0].caption(f"Selected coordinates: {selected[0]:.6f}, {selected[1]:.6f}")
        cols[1].button("Clear location", key="clear_complaint_point", on_click=lambda: st.session_state.pop("complaint_selected_point", None))
    with st.form("new_complaint_form", clear_on_submit=True):
        title = st.text_input("Issue title *", max_chars=180, placeholder="e.g. Stagnant water behind the bus stop")
        c1, c2, c3 = st.columns(3)
        category = c1.selectbox("Category *", CATEGORIES)
        severity = c2.selectbox("Severity *", SEVERITIES, index=1, help="Use CRITICAL only for an immediate public safety concern.")
        location_choice = c3.radio("Location", ["Use selected map pin", "Location unknown"], index=0 if selected else 1)
        description = st.text_area("What happened? *", max_chars=10000, height=140,
                                   placeholder="Describe what you observed, where it is, when it started, and whether it is ongoing.")
        address_text = st.text_input("Landmark / address (avoid house numbers)", value=st.session_state.get("complaint_selected_address", ""), max_chars=300)
        department_map = {"Sanitation": "Sanitation / SWM", "Water & drainage": "Water & Drainage", "Vector risk": "Public Health",
                          "Waste dumping": "Sanitation / SWM", "Road & infrastructure": "Roads & Infrastructure", "Public facility": "Public Works", "Other": "General"}
        st.caption(f"Initial routing department: {department_map.get(category, 'General')} · Jurisdiction: {user.jurisdiction}")
        media = st.file_uploader("Evidence photos / audio / short video (optional, 10 MB per file)", type=["jpg", "jpeg", "png", "webp", "mp4", "webm", "mp3", "wav", "m4a", "ogg"], accept_multiple_files=True)
        consent = st.checkbox("I confirm that this report is truthful to the best of my knowledge and I consent to use of the supplied data for civic response.")
        submitted = st.form_submit_button("Submit report", type="primary", use_container_width=True)
    if submitted:
        if not title.strip() or len(description.strip()) < 10:
            st.error("Add a title and at least 10 characters describing the issue.")
            return
        if not consent:
            st.error("Please confirm the consent statement before submitting.")
            return
        if location_choice == "Use selected map pin" and not selected:
            st.error("Click the map to select a location, search an address, or explicitly select Location unknown.")
            return
        total_bytes = sum(len(f.getvalue()) for f in (media or []))
        if total_bytes > 25 * 1024 * 1024:
            st.error("Combined attachments must be 25 MB or less.")
            return
        try:
            complaint = create_complaint(db, user, title=title, category=category, description=description,
                severity=severity, department=department_map.get(category, "General"), jurisdiction=user.jurisdiction,
                latitude=selected[0] if location_choice == "Use selected map pin" else None,
                longitude=selected[1] if location_choice == "Use selected map pin" else None,
                address_text=address_text, location_status="APPROXIMATE" if location_choice == "Use selected map pin" else "UNKNOWN")
            saved_media, image_ai = [], None
            for f in (media or []):
                media_id = _save_media_for_complaint(db, complaint, user, f)
                saved_media.append(media_id)
                if image_ai is None and str(getattr(f, "type", "")).startswith("image/"):
                    image_ai = analyze_image(f.getvalue(), f.type, title, description, category)
            assessment = image_ai or analyze_text(title, description, category, len(saved_media))
            complaint.ai_assessment_json = json.dumps(assessment, ensure_ascii=False)
            complaint.requires_human_review = True
            log_audit(db, user, "AI_TRIAGE_ATTACHED", "COMPLAINT", complaint.id,
                      {"provider": assessment.get("provider"), "review_status": assessment.get("review_status", "NEEDS_HUMAN_REVIEW")})
            db.commit()
            st.session_state.pop("complaint_selected_point", None)
            st.session_state.pop("complaint_selected_address", None)
            st.success(f"Report submitted successfully. Tracking ID: {complaint.tracking_number}")
            st.info("AI-assisted triage is advisory only. Your report remains in the human review workflow.")
            with st.expander("AI triage review details"):
                st.json(assessment)
        except (ValueError, PermissionError, RuntimeError) as exc:
            db.rollback()
            st.error(str(exc))
        except Exception as exc:
            db.rollback()
            st.error(f"Report registration encountered an error ({type(exc).__name__}). No success message was shown. If the complaint was already saved before an attachment error, check My Reports before resubmitting.")


def render_case_table(db: Session, user: User, *, show_actions: bool = True) -> None:
    cases = list_complaints(db, user)
    if not cases:
        st.info("No reports found for this account and jurisdiction.")
        return
    st.dataframe(pd.DataFrame([{"Case ID": c.tracking_number, "Title": c.title, "Category": c.category,
                                "Severity": c.severity, "Status": c.status, "Department": c.department,
                                "Ward / jurisdiction": c.jurisdiction, "Created": c.created_at,
                                "Due": c.due_at or "—", "Location": "Unknown" if c.latitude is None else (f"{round(c.latitude,2):.2f}, {round(c.longitude,2):.2f}" if user.role == "REPRESENTATIVE" else f"{c.latitude:.4f}, {c.longitude:.4f}")}
                               for c in cases]), hide_index=True, use_container_width=True)
    if not show_actions:
        return
    choices = {f"{c.tracking_number} · {c.title[:70]} · {c.status}": c.id for c in cases}
    selected_label = st.selectbox("Open case details", list(choices), key=f"open_case_{user.id}")
    case = get_complaint(db, user, choices[selected_label])
    with st.container(border=True):
        st.markdown(f"### {case.tracking_number} — {case.title}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Severity", case.severity)
        c2.metric("Status", case.status.replace("_", " "))
        c3.metric("Department", case.department)
        c4.metric("Location", "Unknown" if case.latitude is None else ("Approximate" if user.role == "REPRESENTATIVE" else "Available to authorised users"))
        st.write(case.description)
        st.caption(f"Created {case.created_at} · SLA due {case.due_at or 'not set'}")
        if case.resolution_note:
            st.markdown(f"**Resolution note:** {case.resolution_note}")
        history = db.query(StatusHistory).filter_by(complaint_id=case.id).order_by(StatusHistory.created_at).all()
        with st.expander("Status timeline", expanded=False):
            for h in history:
                st.write(f"**{h.created_at}** · {h.previous_status or '—'} → {h.new_status} · {h.remarks}")
        evidence_rows = db.query(ComplaintMedia).filter_by(complaint_id=case.id).order_by(ComplaintMedia.created_at).all()
        if evidence_rows:
            with st.expander(f"Private evidence attachments ({len(evidence_rows)})", expanded=False):
                st.caption("Downloads are available only because this account is authorised to view this case.")
                for media_row in evidence_rows:
                    ec1, ec2 = st.columns([.7, .3])
                    ec1.write(media_row.original_name)
                    ec1.caption(f"{media_row.mime_type} · {media_row.byte_size:,} bytes · uploaded {media_row.created_at}")
                    try:
                        file_bytes, file_mime = read_private_upload(media_row.stored_name)
                        ec2.download_button("Download evidence", data=file_bytes, file_name=media_row.original_name,
                                            mime=file_mime, key=f"evidence_dl_{media_row.id}", use_container_width=True)
                    except Exception as exc:
                        ec2.caption(f"Unavailable in this deployment ({type(exc).__name__}).")
        assessment = {}
        try:
            assessment = json.loads(case.ai_assessment_json or "{}")
        except Exception:
            pass
        if assessment:
            with st.expander("AI / rules-based triage (advisory only)"):
                st.json(assessment)
        reports = list_reports_for_user(db, user, case.id)
        submitted_reports = [r for r in reports if r.is_submitted]
        if submitted_reports:
            report = sorted(submitted_reports, key=lambda r: (r.updated_at, r.revision))[-1]
            officer = db.query(User).filter_by(id=report.officer_id).first()
            pdf = investigation_pdf(case, report, officer.full_name if officer else "")
            st.download_button("Download investigation PDF", data=pdf, file_name=f"{case.tracking_number}-investigation.pdf",
                               mime="application/pdf", key=f"pdf_{case.id}")
        if user.role == "CITIZEN" and case.status in {"RESOLVED", "CLOSED", "REJECTED_WITH_REASON"}:
            with st.form(f"reopen_{case.id}"):
                reason = st.text_area("Request reopening / appeal reason", max_chars=1500)
                reopen = st.form_submit_button("Request reopening")
            if reopen:
                try:
                    transition_complaint(db, user, case.id, "REOPENED", reason or "Citizen requested a review.")
                    st.success("Reopen request recorded.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def render_my_reports(db: Session, user: User) -> None:
    hero("My reports", "Track report history, view decisions and download authorised records.")
    render_case_table(db, user)
    cases = list_complaints(db, user)
    st.download_button("Export my authorised reports (CSV)", complaints_csv(cases), "swasthyagrid-reports.csv", "text/csv")


def render_hotspot_map(db: Session, user: User, *, admin: bool = False) -> None:
    hero("Hotspots & advisories", "Published hotspots are persistent records with a validity window, precautions and notification audit trail.")
    hotspots = list_hotspots(db, user, include_inactive=admin)
    display_map(db, user, key="hotspot_view" if not admin else "admin_hotspot_view", show_heatmap=False, height=490)
    if hotspots:
        st.dataframe(pd.DataFrame([{"Title": h.title, "Category": h.category, "Severity": h.severity, "Status": h.status,
                                    "Jurisdiction": h.jurisdiction, "Valid from (UTC)": h.valid_from,
                                    "Expires (UTC)": h.expires_at, "Precautions": h.precautions}
                                   for h in hotspots]), hide_index=True, use_container_width=True)
    else:
        st.info("No active advisories currently match this jurisdiction.")


def render_create_hotspot(db: Session, user: User) -> None:
    hero("Hotspot control room", "Create a draft, validate map geometry, publish an advisory and notify citizens in the matching jurisdiction.")
    st.markdown("<div class='sg-warning'>CivicPulse clusters are candidate civic signals only. Publishing an advisory is an administrative action and must be reviewed; do not label a cluster as a confirmed disease outbreak.</div>", unsafe_allow_html=True)
    display_map(db, user, key="hotspot_create", selectable=True, draw_polygon=True, show_heatmap=True, height=520)
    selected = st.session_state.get("hotspot_create_selected_point")
    drawn = st.session_state.get("hotspot_create_drawn_geojson")
    if selected:
        st.caption(f"Selected map centre: {selected[0]:.6f}, {selected[1]:.6f}")
    if drawn:
        st.success("Polygon boundary captured from the map. Select Polygon in the form to use it.")
    c1, c2 = st.columns([1, 1])
    with c1:
        title = st.text_input("Hotspot title", key="hs_title")
        category = st.selectbox("Category", CATEGORIES, key="hs_category")
        severity = st.selectbox("Severity", SEVERITIES, index=2, key="hs_severity")
        geometry_type = st.selectbox("Geometry", ["Circle", "Point", "Polygon"], index=2 if drawn else 0, key="hs_geometry")
        radius = st.slider("Radius for circle (metres)", 50, 5000, 500, 50, key="hs_radius")
    with c2:
        jurisdiction = st.text_input("Jurisdiction / ward (must match registered citizen locality)", value=user.jurisdiction, key="hs_jurisdiction")
        source = st.text_input("Evidence source", value="Administrative field assessment", key="hs_source")
        description = st.text_area("Description", key="hs_description", max_chars=10000)
        precautions = st.text_area("Approved precautions", key="hs_precautions", max_chars=5000,
                                   placeholder="Only include reviewed, practical public guidance. Link to official sources where appropriate.")
    now = datetime.now(timezone.utc)
    t1, t2, t3, t4 = st.columns(4)
    valid_day = t1.date_input("Valid from date (UTC)", value=now.date(), key="hs_from_day")
    valid_clock = t2.time_input("Valid from time (UTC)", value=now.time().replace(tzinfo=None, second=0, microsecond=0), key="hs_from_clock")
    expiry_day = t3.date_input("Expiry date (UTC)", value=(now + timedelta(days=7)).date(), key="hs_expiry_day")
    expiry_clock = t4.time_input("Expiry time (UTC)", value=dt_time(23, 59), key="hs_expiry_clock")
    if geometry_type == "Polygon":
        geometry_geojson = drawn
        if not geometry_geojson:
            st.warning("Draw a polygon/rectangle on the map above, or paste GeoJSON coordinates below.")
            pasted = st.text_area("Polygon GeoJSON (optional)", key="hs_polygon_json", placeholder='{"type":"Polygon","coordinates":[[[76.95,11.01],[76.96,11.01],[76.96,11.02],[76.95,11.02],[76.95,11.01]]]}')
            try:
                geometry_geojson = json.loads(pasted) if pasted.strip() else None
            except json.JSONDecodeError:
                geometry_geojson = None
    elif geometry_type in {"Circle", "Point"}:
        geometry_geojson = {"type": "Point", "coordinates": [float(selected[1]), float(selected[0])]} if selected else None
    else:
        geometry_geojson = None
    if st.button("Save draft hotspot", key="save_hs_draft", use_container_width=True):
        _create_hotspot_record(db, user, title, category, geometry_type, geometry_geojson, selected, radius, severity,
                               description, source, precautions, jurisdiction, valid_day, valid_clock, expiry_day, expiry_clock, publish=False)
    if st.button("Validate and publish advisory", key="publish_hs", type="primary", use_container_width=True):
        _create_hotspot_record(db, user, title, category, geometry_type, geometry_geojson, selected, radius, severity,
                               description, source, precautions, jurisdiction, valid_day, valid_clock, expiry_day, expiry_clock, publish=True)
    st.divider()
    st.subheader("Manage hotspot lifecycle")
    hotspots = list_hotspots(db, user, include_inactive=True)
    if hotspots:
        choices = {f"{h.title} · {h.severity} · {h.status} · {h.id[:8]}": h for h in hotspots}
        label = st.selectbox("Select hotspot", list(choices), key="hs_lifecycle_pick")
        chosen = choices[label]
        c1, c2, c3 = st.columns([.55, .2, .25])
        c1.caption(f"Created: {chosen.created_at} · Expires: {chosen.expires_at}")
        action = c2.selectbox("Action", ["Publish", "Close", "Expire"], key="hs_lifecycle_action")
        if c3.button("Apply lifecycle action", key="hs_lifecycle_apply", use_container_width=True):
            try:
                status = {"Publish": "ACTIVE", "Close": "CLOSED", "Expire": "EXPIRED"}[action]
                set_hotspot_status(db, user, chosen.id, status)
                st.success(f"Hotspot lifecycle updated: {status}.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        with st.expander("Edit selected hotspot details", expanded=False):
            try:
                saved_geometry = json.loads(chosen.geometry_geojson)
                saved_start = datetime.fromisoformat(chosen.valid_from.replace("Z", "+00:00"))
                saved_expiry = datetime.fromisoformat(chosen.expires_at.replace("Z", "+00:00"))
            except Exception:
                saved_geometry = {}
                saved_start = datetime.now(timezone.utc)
                saved_expiry = saved_start + timedelta(days=7)
            st.caption("Edits to a published advisory create an audit event and notify the affected jurisdiction. Closed/expired advisories remain immutable.")
            with st.form(f"hs_edit_form_{chosen.id}"):
                edit_title = st.text_input("Title", value=chosen.title, key=f"hs_edit_title_{chosen.id}")
                edit_category = st.selectbox("Category", CATEGORIES, index=CATEGORIES.index(chosen.category) if chosen.category in CATEGORIES else len(CATEGORIES)-1, key=f"hs_edit_category_{chosen.id}")
                edit_severity = st.selectbox("Severity", SEVERITIES, index=SEVERITIES.index(chosen.severity) if chosen.severity in SEVERITIES else 1, key=f"hs_edit_severity_{chosen.id}")
                edit_geometry_type = st.selectbox("Geometry type", ["Circle", "Point", "Polygon"], index=["Circle", "Point", "Polygon"].index(chosen.geometry_type) if chosen.geometry_type in {"Circle", "Point", "Polygon"} else 0, key=f"hs_edit_geom_type_{chosen.id}")
                edit_radius = st.number_input("Radius (metres; used for circles)", min_value=50, max_value=20000, value=max(50, min(20000, int(chosen.radius_m))), step=50, key=f"hs_edit_radius_{chosen.id}")
                edit_jurisdiction = st.text_input("Jurisdiction / ward", value=chosen.jurisdiction, key=f"hs_edit_jurisdiction_{chosen.id}")
                edit_source = st.text_input("Evidence source", value=chosen.evidence_source, key=f"hs_edit_source_{chosen.id}")
                edit_description = st.text_area("Description", value=chosen.description, max_chars=10000, key=f"hs_edit_description_{chosen.id}")
                edit_precautions = st.text_area("Approved precautions", value=chosen.precautions, max_chars=5000, key=f"hs_edit_precautions_{chosen.id}")
                e1, e2, e3, e4 = st.columns(4)
                edit_start_day = e1.date_input("Valid from date (UTC)", value=saved_start.date(), key=f"hs_edit_start_day_{chosen.id}")
                edit_start_clock = e2.time_input("Valid from time (UTC)", value=saved_start.time().replace(tzinfo=None), key=f"hs_edit_start_time_{chosen.id}")
                edit_expiry_day = e3.date_input("Expiry date (UTC)", value=saved_expiry.date(), key=f"hs_edit_expiry_day_{chosen.id}")
                edit_expiry_clock = e4.time_input("Expiry time (UTC)", value=saved_expiry.time().replace(tzinfo=None), key=f"hs_edit_expiry_time_{chosen.id}")
                use_new_point = st.checkbox("Use the latest point clicked on the map above for Point/Circle", value=False, key=f"hs_edit_use_point_{chosen.id}")
                use_drawn_polygon = st.checkbox("Use the latest polygon drawn on the map above", value=False, key=f"hs_edit_use_polygon_{chosen.id}")
                polygon_text_default = json.dumps(saved_geometry, ensure_ascii=False) if chosen.geometry_type == "Polygon" else ""
                edit_polygon_text = st.text_area("Polygon GeoJSON (edit directly if required)", value=polygon_text_default, key=f"hs_edit_polygon_{chosen.id}", help="Use a GeoJSON Polygon geometry. The server validates geometry and coordinate bounds.")
                save_edits = st.form_submit_button("Save hotspot edits", type="primary", use_container_width=True)
            if save_edits:
                try:
                    selected_point = st.session_state.get("hotspot_create_selected_point") if use_new_point else (chosen.center_latitude, chosen.center_longitude)
                    if edit_geometry_type in {"Point", "Circle"}:
                        geometry_update = {"type": "Point", "coordinates": [float(selected_point[1]), float(selected_point[0])]} if selected_point else None
                        center_lat, center_lon = selected_point if selected_point else (chosen.center_latitude, chosen.center_longitude)
                    else:
                        drawn_update = st.session_state.get("hotspot_create_drawn_geojson") if use_drawn_polygon else None
                        geometry_update = drawn_update
                        if geometry_update is None and edit_polygon_text.strip():
                            geometry_update = json.loads(edit_polygon_text)
                        center_lat, center_lon = chosen.center_latitude, chosen.center_longitude
                        if geometry_update:
                            polygon = shape(geometry_update)
                            center_lat, center_lon = polygon.centroid.y, polygon.centroid.x
                    if not geometry_update:
                        raise ValueError("A valid point or polygon geometry is required.")
                    updated = update_hotspot(db, user, chosen.id, title=edit_title, category=edit_category,
                        geometry_type=edit_geometry_type, geometry_geojson=geometry_update,
                        center_latitude=center_lat, center_longitude=center_lon, radius_m=int(edit_radius),
                        severity=edit_severity, description=edit_description, evidence_source=edit_source,
                        precautions=edit_precautions, jurisdiction=edit_jurisdiction,
                        valid_from=utc_iso(edit_start_day, edit_start_clock), expires_at=utc_iso(edit_expiry_day, edit_expiry_clock))
                    st.success(f"Hotspot {updated.id[:8]} updated and audited.")
                    st.rerun()
                except Exception as exc:
                    db.rollback()
                    st.error(f"Hotspot was not updated: {exc}")
    else:
        st.info("No hotspot records have been created yet.")


def _create_hotspot_record(db: Session, user: User, title: str, category: str, geometry_type: str,
                           geometry_geojson: dict[str, Any] | None, selected: tuple[float, float], radius: int,
                           severity: str, description: str, source: str, precautions: str, jurisdiction: str,
                           valid_day: date, valid_clock: dt_time, expiry_day: date, expiry_clock: dt_time,
                           publish: bool) -> None:
    if not geometry_geojson:
        st.error("Select a point on the map or provide a valid polygon boundary before saving.")
        return
    if geometry_type in {"Circle", "Point"} and not selected:
        st.error("Select the actual hotspot location on the map. The map's starting centre is not a selected location.")
        return
    try:
        if geometry_type == "Polygon":
            polygon = shape(geometry_geojson)
            if not polygon.is_valid or polygon.is_empty:
                raise ValueError("The drawn polygon is invalid.")
            center_lat, center_lon = polygon.centroid.y, polygon.centroid.x
        else:
            center_lat, center_lon = selected
        hotspot = publish_hotspot(db, user, title=title, category=category, geometry_type=geometry_type,
            geometry_geojson=geometry_geojson, center_latitude=center_lat, center_longitude=center_lon,
            radius_m=radius, severity=severity, description=description, evidence_source=source,
            precautions=precautions, jurisdiction=jurisdiction, valid_from=utc_iso(valid_day, valid_clock),
            expires_at=utc_iso(expiry_day, expiry_clock), publish=publish)
        st.success(f"Hotspot saved with ID {hotspot.id[:8]} · Status: {hotspot.status}.")
        if publish and hotspot.status != "ACTIVE":
            st.warning("The advisory was saved as a draft because its valid-from/expiry window is not active right now.")
        st.rerun()
    except Exception as exc:
        db.rollback()
        st.error(f"Hotspot was not saved: {exc}")


def render_admin_complaints(db: Session, user: User) -> None:
    hero("Complaint operations", "Assign cases to officers, validate status transitions, and manage resolution/escalation using audited actions.")
    cases = list_complaints(db, user)
    render_case_table(db, user, show_actions=False)
    if not cases:
        return
    choices = {f"{c.tracking_number} · {c.title[:60]} · {c.status}": c for c in cases}
    label = st.selectbox("Select a case to administer", list(choices), key="admin_case_select")
    case = choices[label]
    with st.container(border=True):
        st.markdown(f"### {case.tracking_number}: {case.title}")
        officers = db.query(User).filter_by(role="FIELD_OFFICER", is_active=True, jurisdiction=case.jurisdiction).order_by(User.full_name).all()
        if officers:
            officer_choices = {f"{o.full_name} · {o.department}": o.id for o in officers}
            picked = st.selectbox("Assign field officer (same jurisdiction)", list(officer_choices), key=f"assign_{case.id}")
            if st.button("Assign case", key=f"assign_btn_{case.id}"):
                try:
                    assign_complaint(db, user, case.id, officer_choices[picked])
                    st.success("Case assignment saved and the officer has been notified.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.warning("No active Field Officer exists in this jurisdiction. Create one from User management first.")
        transitions = sorted(TRANSITIONS.get(case.status, set()))
        if transitions:
            with st.form(f"admin_transition_{case.id}"):
                next_status = st.selectbox("New state", transitions)
                remarks = st.text_area("Audit note / rejection reason", max_chars=2000)
                resolution = st.text_area("Resolution note (when applicable)", max_chars=5000)
                apply = st.form_submit_button("Apply status transition")
            if apply:
                try:
                    transition_complaint(db, user, case.id, next_status, remarks, resolution)
                    st.success(f"Status changed to {next_status}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.info("This case has no permitted outgoing transitions.")
        with st.expander("Review investigation reports"):
            reports = list_reports_for_user(db, user, case.id)
            if reports:
                for r in reports:
                    st.write(f"**Revision {r.revision}** · {'Submitted' if r.is_submitted else 'Draft'} · {r.updated_at}")
                    st.write(f"Findings: {r.findings}")
                    st.write(f"Action taken: {r.action_taken or 'Not recorded'}")
            else:
                st.caption("No officer report has been saved for this case.")
        with st.expander("Create cross-department dependency"):
            with st.form(f"dependency_{case.id}"):
                target = st.selectbox("Target department", DEPARTMENTS, key=f"dep_target_{case.id}")
                dep_desc = st.text_area("Dependency / follow-up required", key=f"dep_desc_{case.id}")
                due = st.date_input("Follow-up due date", value=date.today() + timedelta(days=3), key=f"dep_due_{case.id}")
                dep_submit = st.form_submit_button("Record dependency")
            if dep_submit:
                try:
                    assign_dependency(db, user, case.id, target, dep_desc, due.isoformat())
                    st.success("Department dependency recorded.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def render_field_officer(db: Session, user: User) -> None:
    hero("Field investigations", "Only cases assigned to you in your jurisdiction are visible. Resolution requires a submitted investigation report.")
    cases = list_complaints(db, user)
    if not cases:
        st.info("No cases are assigned to you yet. The admin will assign cases from the complaint operations screen.")
        display_map(db, user, key="officer_empty_map", show_heatmap=False, height=420)
        return
    render_map_page(db, user, selection_key="officer_map", selectable=False)
    st.subheader("Assigned case queue")
    choices = {f"{c.tracking_number} · {c.title[:70]} · {c.status}": c for c in cases}
    label = st.selectbox("Choose a case to inspect", list(choices), key=f"officer_case_{user.id}")
    case = choices[label]
    with st.container(border=True):
        st.markdown(f"### {case.tracking_number} — {case.title}")
        st.write(case.description)
        st.caption(f"Severity: {case.severity} · Department: {case.department} · Due: {case.due_at or 'Not set'}")
        allowed = [x for x in sorted(TRANSITIONS.get(case.status, set())) if x in {"UNDER_INVESTIGATION", "ACTION_IN_PROGRESS", "RESOLVED", "ESCALATED", "REOPENED"}]
        if allowed:
            with st.form(f"officer_status_{case.id}"):
                new_status = st.selectbox("Case workflow action", allowed)
                notes = st.text_input("Status note")
                action_submit = st.form_submit_button("Update case state")
            if action_submit:
                try:
                    transition_complaint(db, user, case.id, new_status, notes)
                    st.success(f"Case moved to {new_status}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        st.markdown("#### Investigation report form")
        drafts = [r for r in list_reports_for_user(db, user, case.id) if not r.is_submitted]
        draft_options = {"Create new report": None}
        draft_options.update({f"Edit draft revision {r.revision} · {r.id[:8]}": r for r in drafts})
        draft_label = st.selectbox("Report version", list(draft_options), key=f"report_version_{case.id}")
        draft = draft_options[draft_label]
        with st.form(f"investigation_form_{case.id}"):
            now = datetime.now(timezone.utc)
            a1, a2, a3 = st.columns(3)
            inspection_day = a1.date_input("Inspection date (UTC)", value=now.date(), key=f"insp_day_{case.id}")
            inspection_clock = a2.time_input("Inspection time (UTC)", value=now.time().replace(tzinfo=None, second=0, microsecond=0), key=f"insp_time_{case.id}")
            severity = a3.selectbox("Assessed severity", SEVERITIES, index=SEVERITIES.index(draft.severity) if draft and draft.severity in SEVERITIES else SEVERITIES.index(case.severity), key=f"insp_sev_{case.id}")
            has_coords = st.checkbox("Record inspection coordinates", value=bool(draft and draft.inspection_latitude is not None), key=f"insp_has_coords_{case.id}")
            lc1, lc2 = st.columns(2)
            inspect_lat = lc1.number_input("Inspection latitude", min_value=-90.0, max_value=90.0, value=float(draft.inspection_latitude) if draft and draft.inspection_latitude is not None else float(case.latitude or DEFAULT_CENTER[0]), format="%.6f", key=f"insp_lat_{case.id}")
            inspect_lon = lc2.number_input("Inspection longitude", min_value=-180.0, max_value=180.0, value=float(draft.inspection_longitude) if draft and draft.inspection_longitude is not None else float(case.longitude or DEFAULT_CENTER[1]), format="%.6f", key=f"insp_lon_{case.id}")
            reported_problem = st.text_area("1. Complainant's reported problem *", value=draft.reported_problem if draft else case.description, key=f"insp_problem_{case.id}")
            observations = st.text_area("2. Actual site observations *", value=draft.site_observations if draft else "", key=f"insp_obs_{case.id}")
            findings = st.text_area("3. Findings and likely contributing factors *", value=draft.findings if draft else "", key=f"insp_findings_{case.id}")
            action = st.text_area("4. Action taken", value=draft.action_taken if draft else "", key=f"insp_action_{case.id}")
            resources = st.text_input("5. Materials / resources used", value=draft.resources_used if draft else "", key=f"insp_resources_{case.id}")
            referral = st.text_input("6. Department referral / responsible contact", value=draft.department_referral if draft else "", key=f"insp_referral_{case.id}")
            pending = st.text_area("7. Work pending", value=draft.pending_work if draft else "", key=f"insp_pending_{case.id}")
            pending_reason = st.text_input("8. Reason work is pending", value=draft.pending_reason if draft else "", key=f"insp_pending_reason_{case.id}")
            recommendation = st.text_area("9. Recommended corrective action *", value=draft.recommended_action if draft else "", key=f"insp_recommendation_{case.id}")
            followup = st.date_input("10. Follow-up date", value=date.fromisoformat(draft.follow_up_date) if draft and draft.follow_up_date else date.today() + timedelta(days=7), key=f"insp_followup_{case.id}")
            resolution_status = st.selectbox("11. Resolution status", ["IN_PROGRESS", "ACTION_COMPLETED", "NEEDS_DEPARTMENT", "FOLLOW_UP_REQUIRED", "RESOLVED"], index=0, key=f"insp_resolution_{case.id}")
            feedback = st.text_area("12. Citizen feedback / contact outcome", value=draft.citizen_feedback if draft else "", key=f"insp_feedback_{case.id}")
            before_files = st.file_uploader("Before-action evidence (photo/audio/video)", type=["jpg", "jpeg", "png", "webp", "mp4", "webm", "mp3", "wav", "m4a", "ogg"], accept_multiple_files=True, key=f"insp_before_{case.id}")
            after_files = st.file_uploader("After-action evidence (photo/audio/video)", type=["jpg", "jpeg", "png", "webp", "mp4", "webm", "mp3", "wav", "m4a", "ogg"], accept_multiple_files=True, key=f"insp_after_{case.id}")
            declaration = st.checkbox("I declare that this report accurately records my inspection and evidence.", value=bool(draft and draft.declaration), key=f"insp_decl_{case.id}")
            b1, b2 = st.columns(2)
            save_draft = b1.form_submit_button("Save draft", use_container_width=True)
            submit_report = b2.form_submit_button("Submit report", type="primary", use_container_width=True)
        if save_draft or submit_report:
            if submit_report and not declaration:
                st.error("The officer declaration is required to submit a report.")
            elif any(len(f.getvalue()) > MAX_FILE_BYTES for f in ((before_files or []) + (after_files or []))):
                st.error(f"Each evidence file must be no larger than {MAX_FILE_BYTES // (1024*1024)} MB.")
            else:
                refs = []
                try:
                    for f in (before_files or []): refs.append("BEFORE:" + _save_media_for_complaint(db, case, user, f, label="before"))
                    for f in (after_files or []): refs.append("AFTER:" + _save_media_for_complaint(db, case, user, f, label="after"))
                    report = submit_investigation_report(db, user, complaint_id=case.id,
                        inspection_at=utc_iso(inspection_day, inspection_clock),
                        inspection_latitude=float(inspect_lat) if has_coords else None,
                        inspection_longitude=float(inspect_lon) if has_coords else None,
                        reported_problem=reported_problem, site_observations=observations, findings=findings, severity=severity,
                        evidence_refs=refs, action_taken=action, resources_used=resources, department_referral=referral,
                        pending_work=pending, pending_reason=pending_reason, recommended_action=recommendation,
                        follow_up_date=followup.isoformat(), resolution_status=resolution_status,
                        citizen_feedback=feedback, declaration=declaration, submit=bool(submit_report),
                        report_id=draft.id if draft else None)
                    st.success("Investigation report submitted." if submit_report else "Draft saved. The report can be revised before submission.")
                    st.rerun()
                except Exception as exc:
                    db.rollback()
                    st.error(str(exc))
        report_rows = list_reports_for_user(db, user, case.id)
        sent_reports = [r for r in report_rows if r.is_submitted]
        for rep in sent_reports:
            pdf = investigation_pdf(case, rep, user.full_name)
            st.download_button(f"Download submitted report revision {rep.revision}", pdf,
                               f"{case.tracking_number}-revision-{rep.revision}.pdf", "application/pdf", key=f"officer_pdf_{rep.id}")


def render_users(db: Session, user: User) -> None:
    hero("User & jurisdiction management", "Provision staff accounts, manage role scope and suspend compromised or departed accounts.")
    with st.expander("Create account", expanded=True):
        with st.form("admin_create_user"):
            full_name = st.text_input("Full name")
            email = st.text_input("Email")
            password = st.text_input("Temporary password (minimum 10 characters)", type="password")
            role = st.selectbox("Role", ["CITIZEN", "FIELD_OFFICER", "REPRESENTATIVE", "ADMIN"])
            jurisdiction = st.text_input("Jurisdiction / ward", value=user.jurisdiction)
            department = st.selectbox("Department", DEPARTMENTS)
            create = st.form_submit_button("Create account", type="primary")
        if create:
            try:
                created = create_user(db, email, password, full_name, role, jurisdiction, department, actor=user)
                st.success(f"Created {created.role} account for {created.email}. Share the temporary password securely and instruct them to change it; self-service recovery requires SMTP configuration.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    all_users = db.query(User).order_by(User.created_at.desc()).limit(1000).all()
    st.subheader("Registered accounts")
    st.dataframe(pd.DataFrame([{"Name": u.full_name, "Email": u.email, "Role": u.role, "Jurisdiction": u.jurisdiction,
                                "Department": u.department, "Active": u.is_active, "Created": u.created_at} for u in all_users]),
                 hide_index=True, use_container_width=True)
    targets = [u for u in all_users if u.id != user.id]
    if targets:
        picks = {f"{u.full_name} · {u.role} · {u.email} · {'active' if u.is_active else 'suspended'}": u for u in targets}
        choice = st.selectbox("Account to activate/suspend", list(picks), key="user_suspend_select")
        target = picks[choice]
        if st.button("Suspend account" if target.is_active else "Reactivate account", key="suspend_user_btn"):
            target.is_active = not target.is_active
            target.updated_at = iso_now()
            log_audit(db, user, "USER_ACTIVE_STATE_CHANGED", "USER", target.id, {"is_active": target.is_active})
            db.commit()
            st.success(f"Account is now {'active' if target.is_active else 'suspended'}.")
            st.rerun()


def render_civicpulse(db: Session, user: User) -> None:
    hero("CivicPulse signal engine", "Transparent report clustering surfaces repeated local issues for human review; it does not confirm disease or causation.")
    c1, c2, c3 = st.columns(3)
    days = c1.slider("Observation window (days)", 1, 30, 14)
    radius = c2.slider("Cluster radius (metres)", 100, 3000, 1000, 100)
    threshold = c3.slider("Minimum distinct reports", 3, 10, 3)
    signals = compute_signals(db, days=days, radius_m=radius, threshold=threshold, persist=user.role == "ADMIN")
    if user.role in {"FIELD_OFFICER", "REPRESENTATIVE"}:
        signals = [s for s in signals if s["jurisdiction"] == user.jurisdiction]
    if not signals:
        st.info("No report cluster crossed this rule threshold in the selected observation window.")
    for s in signals:
        with st.container(border=True):
            st.markdown(f"### {s['category']} · {s['jurisdiction']}")
            st.caption(s["label"])
            c1, c2, c3 = st.columns(3)
            c1.metric("Distinct reports", s["report_count"])
            c2.metric("Signal severity", s["severity"])
            c3.metric("Radius", f"{s['radius_m']} m")
            st.write("Evidence:")
            st.dataframe(pd.DataFrame(s["evidence"]), hide_index=True, use_container_width=True)
            st.caption(f"Approximate centre: {s['center_latitude']:.4f}, {s['center_longitude']:.4f} · Status: {s['status']}")
            if user.role == "ADMIN" and not str(s["id"]).startswith("preview-"):
                with st.form(f"review_signal_{s['id']}"):
                    review_status = st.selectbox("Human review decision", ["ACKNOWLEDGED", "DISMISSED", "ACTION_CREATED"], key=f"sig_status_{s['id']}")
                    notes = st.text_area("Review notes and rationale", key=f"sig_notes_{s['id']}")
                    review = st.form_submit_button("Save review")
                if review:
                    try:
                        review_signal(db, user, s["id"], review_status, notes)
                        st.success("CivicPulse review saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))


def render_rep_followups(db: Session, user: User) -> None:
    hero("Constituency follow-ups", "Review authorised jurisdiction cases, escalations, investigation findings and inter-department dependencies.")
    render_case_table(db, user)
    deps = db.query(CaseDependency).join(Complaint, Complaint.id == CaseDependency.complaint_id)
    deps = deps.filter(Complaint.jurisdiction == user.jurisdiction).order_by(CaseDependency.created_at.desc()).limit(300).all()
    st.subheader("Cross-department dependency tracker")
    if deps:
        st.dataframe(pd.DataFrame([{"Case ID": d.complaint_id, "From": d.source_department, "To": d.target_department,
                                    "Dependency": d.description, "Status": d.status, "Due": d.due_at or "—", "Created": d.created_at} for d in deps]),
                     hide_index=True, use_container_width=True)
    else:
        st.info("No department dependencies have been recorded for this jurisdiction.")
    cases = list_complaints(db, user)
    if cases:
        choices = {f"{c.tracking_number} · {c.title}": c for c in cases}
        selected = st.selectbox("Escalate a case", list(choices), key="rep_escalate_case")
        case = choices[selected]
        if st.button("Escalate to administrator", key="rep_escalate_btn"):
            try:
                if case.status == "ESCALATED":
                    st.info("This case is already escalated.")
                else:
                    transition_complaint(db, user, case.id, "ESCALATED", "Escalated by jurisdiction representative for official follow-up.")
                    st.success("Escalation recorded; authorised staff and the citizen were notified.")
                    st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_reports_export(db: Session, user: User) -> None:
    hero("Reports & exports", "Download reports scoped to your permissions. Public-health maps use aggregated or approximate data.")
    cases = list_complaints(db, user)
    st.metric("Records available to this role", len(cases))
    st.download_button("Download authorised complaints CSV", data=complaints_csv(cases),
                       file_name="swasthyagrid-authorised-complaints.csv", mime="text/csv", use_container_width=True)
    reports = list_reports_for_user(db, user)
    if reports:
        st.subheader("Investigation report exports")
        choices = {}
        for r in reports:
            c = db.query(Complaint).filter_by(id=r.complaint_id).first()
            if c and can_access_complaint(user, c):
                choices[f"{c.tracking_number} · revision {r.revision} · {'submitted' if r.is_submitted else 'draft'}"] = (c, r)
        if choices:
            label = st.selectbox("Select investigation report", list(choices), key="export_report_select")
            case, report = choices[label]
            if report.is_submitted:
                officer = db.query(User).filter_by(id=report.officer_id).first()
                st.download_button("Download investigation PDF", investigation_pdf(case, report, officer.full_name if officer else ""),
                                   f"{case.tracking_number}-investigation.pdf", "application/pdf")
            else:
                st.info("Draft reports are not offered as official PDF exports. Submit the report after review.")
    else:
        st.info("No investigation reports are available to this role yet.")


def render_admin_audit(db: Session, user: User) -> None:
    hero("Audit & system health", "Review privileged actions, check the append-only hash chain and validate service/database readiness.")
    factory = get_session_factory()
    try:
        factory().execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ready = True
    except Exception:
        db_ready = False
    c1, c2, c3 = st.columns(3)
    c1.metric("Application", "Ready")
    c2.metric("Database", "Connected" if db_ready else "Error")
    c3.metric("AI provider", "Configured" if os.getenv("GEMINI_API_KEY") else "Rules fallback")
    if st.button("Verify audit hash chain", type="primary"):
        okay, count = verify_audit_chain(db)
        (st.success if okay else st.error)(f"Audit chain {'valid' if okay else 'FAILED'} · {count} event(s) checked.")
    events = db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(300).all()
    if events:
        st.dataframe(pd.DataFrame([{"Time": e.created_at, "Actor role": e.actor_role, "Action": e.action,
                                    "Entity": e.entity_type, "Entity ID": e.entity_id, "Details": e.details_json,
                                    "Previous hash": e.previous_hash[:12], "Event hash": e.event_hash[:12]} for e in events]),
                     hide_index=True, use_container_width=True)
    else:
        st.info("No audit events have been written yet.")
    if st.button("Run due SLA / hotspot jobs now"):
        try:
            outcomes = process_scheduled_tasks(db)
            st.success(f"Scheduled processing complete: {outcomes}")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def render_sla_policies(db: Session, user: User) -> None:
    hero("SLA Guardian policies", "Configure response deadlines and escalation thresholds. Changes are audit logged.")
    policies = db.query(SLAPolicy).order_by(SLAPolicy.severity).all()
    for policy in policies:
        with st.form(f"sla_policy_{policy.severity}"):
            st.markdown(f"**{policy.severity}**")
            a, b = st.columns(2)
            response = a.number_input("Response deadline (hours)", 1, 720, int(policy.response_hours), key=f"sla_resp_{policy.severity}")
            escalation = b.number_input("Escalation after deadline (hours)", 1, 1440, int(policy.escalation_hours), key=f"sla_esc_{policy.severity}")
            save = st.form_submit_button("Save policy")
        if save:
            policy.response_hours, policy.escalation_hours = int(response), int(escalation)
            log_audit(db, user, "SLA_POLICY_UPDATED", "SLA_POLICY", policy.id,
                      {"response_hours": response, "escalation_hours": escalation})
            db.commit()
            st.success(f"{policy.severity} SLA policy updated.")
            st.rerun()


def render_forgot_password(db: Session) -> None:
    hero("Account recovery", "Request a single-use password reset link. SMTP must be configured by the deployment owner.")
    with st.form("forgot_password_form"):
        email = st.text_input("Account email")
        send = st.form_submit_button("Send reset link", type="primary")
    if send:
        target = db.query(User).filter_by(email=email.strip().lower(), is_active=True).first()
        # Response is intentionally generic to prevent account enumeration.
        if target:
            try:
                token = create_password_reset_token(db, target)
                send_password_reset_email(target.email, token)
            except Exception:
                pass
        st.success("If an active account exists and email delivery is configured, a reset link will be sent.")
    if st.button("Back to sign in"):
        st.session_state.pop("sg_page", None)
        st.rerun()


def login_screen(db: Session) -> None:
    if st.query_params.get("reset_token"):
        hero("Set a new password", "This reset link is single-use and expires after 30 minutes.")
        with st.form("reset_password_form"):
            new_password = st.text_input("New password (minimum 10 characters)", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submit = st.form_submit_button("Update password", type="primary")
        if submit:
            token = str(st.query_params.get("reset_token", ""))
            if new_password != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    okay = reset_password(db, token, new_password)
                    if okay:
                        st.query_params.clear()
                        st.success("Password updated. Sign in with your new password.")
                        st.rerun()
                    st.error("The reset link is invalid, expired or already used.")
                except Exception as exc:
                    st.error(str(exc))
        return
    hero("SwasthyaGrid", "Civic intelligence and public-health risk triage for Indian cities.", "Public service · Local signals · Accountable response")
    lcol, rcol = st.columns([1.05, .95], gap="large")
    with lcol:
        st.markdown("### One connected response system")
        st.markdown("- **Citizens:** report issues, choose approximate locations and follow progress.\n- **Field officers:** record inspections, evidence, actions and follow-ups.\n- **Administrators:** publish advisories, assign cases and manage SLAs.\n- **Representatives:** review ward trends and departmental dependencies.")
        st.markdown("<div class='sg-note'>Real OpenStreetMap tiles · Persistent database records · Audited workflow transitions · AI-assisted review with human oversight</div>", unsafe_allow_html=True)
        st.caption("Never use the platform to make medical diagnoses. An automated cluster is a lead for investigation, not proof of an outbreak.")
    with rcol:
        login_tab, register_tab = st.tabs(["Sign in", "Citizen registration"])
        with login_tab:
            with st.form("login_form"):
                email = st.text_input("Email", key="login_email")
                password = st.text_input("Password", type="password", key="login_password")
                submit = st.form_submit_button("Sign in securely", type="primary", use_container_width=True)
            if submit:
                blocked, retry = _login_blocked(db, email)
                if blocked:
                    st.error(f"Too many failed attempts. Try again in about {retry} seconds.")
                else:
                    user = authenticate_user(db, email, password)
                    if user:
                        _clear_login_failures(db, email)
                        st.session_state["sg_user_id"] = user.id
                        st.session_state["sg_user_name"] = user.full_name
                        st.session_state["sg_login_timestamp"] = time.time()
                        st.success("Sign-in successful.")
                        st.rerun()
                    else:
                        _record_login_failure(db, email)
                        st.error("Email or password is incorrect.")
            if st.button("Forgot password?", key="forgot_password_link"):
                st.session_state["sg_page"] = "forgot"
                st.rerun()
        with register_tab:
            with st.form("citizen_registration_form"):
                name = st.text_input("Full name", key="reg_name")
                reg_email = st.text_input("Email", key="reg_email")
                p1 = st.text_input("Password (10+ characters)", type="password", key="reg_p1")
                p2 = st.text_input("Confirm password", type="password", key="reg_p2")
                jurisdiction = st.text_input("Ward / locality", value="Coimbatore, Tamil Nadu", key="reg_jurisdiction")
                register = st.form_submit_button("Create citizen account", type="primary", use_container_width=True)
            if register:
                if p1 != p2:
                    st.error("Passwords do not match.")
                else:
                    try:
                        user = create_user(db, reg_email, p1, name, "CITIZEN", jurisdiction, "Resident Services")
                        st.session_state["sg_user_id"] = user.id
                        st.session_state["sg_user_name"] = user.full_name
                        st.success("Citizen account created.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))


def _login_blocked(db: Session, email: str) -> tuple[bool, int]:
    from backend.security_controls import login_is_blocked
    return login_is_blocked(db, email)

def _record_login_failure(db: Session, email: str) -> None:
    from backend.security_controls import record_login_failure
    record_login_failure(db, email)

def _clear_login_failures(db: Session, email: str) -> None:
    from backend.security_controls import clear_login_failures
    clear_login_failures(db, email)


def show_sidebar(user: User) -> str:
    st.sidebar.markdown("## 🧭 SwasthyaGrid")
    st.sidebar.caption("Civic intelligence platform")
    st.sidebar.markdown(f"**{user.full_name}**\n\n{ROLE_LABELS[user.role]}\n\n📍 {user.jurisdiction}")
    st.sidebar.divider()
    nav = {
        "CITIZEN": ["Overview", "Report an issue", "My reports", "Hotspots & alerts", "Notifications", "Account recovery"],
        "FIELD_OFFICER": ["Overview", "Assigned cases & investigations", "Live map", "Notifications"],
        "ADMIN": ["Overview", "Live map", "Complaints & assignment", "Hotspot control room", "User management", "CivicPulse signals", "Reports & exports", "SLA policies", "Audit & system", "Notifications"],
        "REPRESENTATIVE": ["Overview", "Constituency map", "Reports & follow-ups", "CivicPulse signals", "Notifications"],
    }
    page = st.sidebar.radio("Workspace", nav[user.role], label_visibility="collapsed", key=f"nav_{user.id}")
    st.sidebar.divider()
    st.sidebar.caption("Map data © OpenStreetMap contributors. Geocoding searches are user initiated.")
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.pop("sg_user_id", None)
        st.session_state.pop("sg_user_name", None)
        st.rerun()
    st.sidebar.caption(f"Build {APP_VERSION} · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return page


def maybe_run_scheduled_jobs(db: Session) -> None:
    now = time.time()
    last = st.session_state.get("sg_jobs_last_run", 0)
    if now - last > 300:
        try:
            process_scheduled_tasks(db)
            st.session_state["sg_jobs_last_run"] = now
        except Exception:
            db.rollback()


def main() -> None:
    try:
        init_runtime()
    except Exception as exc:
        hero("SwasthyaGrid could not start", "The database or deployment configuration needs attention.")
        st.error(f"Startup check failed: {type(exc).__name__}: {exc}")
        st.markdown("Configure a valid `DATABASE_URL`. For live deployment use a persistent PostgreSQL database; SQLite is for local development and demos.")
        st.stop()
    if st.session_state.get("sg_bootstrap_message") and not st.session_state.get("sg_bootstrap_notice_shown"):
        st.session_state["sg_bootstrap_notice_shown"] = True
        st.sidebar.info(st.session_state["sg_bootstrap_message"])
    db = get_db()
    try:
        if st.session_state.get("sg_page") == "forgot":
            render_forgot_password(db)
            return
        user = current_user(db)
        if user is None:
            login_screen(db)
            return
        maybe_run_scheduled_jobs(db)
        page = show_sidebar(user)
        if user.role == "CITIZEN":
            if page == "Overview": render_overview(db, user)
            elif page == "Report an issue": render_new_complaint(db, user)
            elif page == "My reports": render_my_reports(db, user)
            elif page == "Hotspots & alerts": render_hotspot_map(db, user)
            elif page == "Notifications": render_notifications(db, user)
            elif page == "Account recovery": render_forgot_password(db)
        elif user.role == "FIELD_OFFICER":
            if page == "Overview": render_overview(db, user)
            elif page == "Assigned cases & investigations": render_field_officer(db, user)
            elif page == "Live map": render_map_page(db, user, selection_key="officer_live_map")
            elif page == "Notifications": render_notifications(db, user)
        elif user.role == "ADMIN":
            if page == "Overview": render_overview(db, user)
            elif page == "Live map": render_map_page(db, user, selection_key="admin_live_map", selectable=True)
            elif page == "Complaints & assignment": render_admin_complaints(db, user)
            elif page == "Hotspot control room": render_create_hotspot(db, user)
            elif page == "User management": render_users(db, user)
            elif page == "CivicPulse signals": render_civicpulse(db, user)
            elif page == "Reports & exports": render_reports_export(db, user)
            elif page == "SLA policies": render_sla_policies(db, user)
            elif page == "Audit & system": render_admin_audit(db, user)
            elif page == "Notifications": render_notifications(db, user)
        elif user.role == "REPRESENTATIVE":
            if page == "Overview": render_overview(db, user)
            elif page == "Constituency map": render_map_page(db, user, selection_key="rep_map")
            elif page == "Reports & follow-ups": render_rep_followups(db, user)
            elif page == "CivicPulse signals": render_civicpulse(db, user)
            elif page == "Notifications": render_notifications(db, user)
    finally:
        db.close()


if __name__ == "__main__":
    main()

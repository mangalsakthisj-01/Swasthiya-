"""Permission-conscious PDF/CSV exports."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from xml.sax.saxutils import escape
from .database import Complaint, InvestigationReport


def safe_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    # Prevent formula injection in Excel/Sheets for user-controlled fields.
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def complaints_csv(complaints: Iterable[Complaint]) -> bytes:
    buf = io.StringIO(newline="")
    fields = ["tracking_number", "title", "category", "severity", "status", "department", "jurisdiction",
              "created_at", "due_at", "location_status", "latitude", "longitude"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for c in complaints:
        row = {f: getattr(c, f, "") for f in fields}
        writer.writerow({k: safe_csv_cell(v) for k, v in row.items()})
    return buf.getvalue().encode("utf-8-sig")


def investigation_pdf(complaint: Complaint, report: InvestigationReport, officer_name: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=17*mm, bottomMargin=17*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SGLabel", parent=styles["BodyText"], textColor=colors.HexColor("#475569"), fontSize=8, leading=11))
    styles.add(ParagraphStyle(name="SGValue", parent=styles["BodyText"], fontSize=9, leading=13, spaceAfter=4))
    story = [Paragraph("SwasthyaGrid", styles["Title"]), Paragraph("Field Investigation Report", styles["Heading2"]),
             Paragraph(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · Revision {report.revision}", styles["SGLabel"]), Spacer(1, 6*mm)]
    meta = [["Case ID", escape(complaint.tracking_number), "Status", escape(complaint.status)],
            ["Category", escape(complaint.category), "Severity", escape(complaint.severity)],
            ["Jurisdiction", escape(complaint.jurisdiction), "Department", escape(complaint.department)],
            ["Inspection time", escape(report.inspection_at), "Officer", escape(officer_name or report.officer_id)]]
    table = Table(meta, colWidths=[26*mm, 55*mm, 25*mm, 55*mm])
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (0,-1), colors.HexColor("#E2E8F0")),
                               ("BACKGROUND", (2,0), (2,-1), colors.HexColor("#E2E8F0")),
                               ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#CBD5E1")),
                               ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONTSIZE", (0,0), (-1,-1), 8),
                               ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
                               ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    story += [table, Spacer(1, 5*mm)]
    fields = [
        ("Reported problem", report.reported_problem), ("Site observations", report.site_observations),
        ("Findings / contributing factors", report.findings), ("Action taken", report.action_taken),
        ("Resources used", report.resources_used), ("Department referral", report.department_referral),
        ("Pending work", report.pending_work), ("Reason pending", report.pending_reason),
        ("Recommended corrective action", report.recommended_action), ("Follow-up date", report.follow_up_date),
        ("Resolution status", report.resolution_status), ("Citizen feedback", report.citizen_feedback),
        ("Inspection coordinates", f"{report.inspection_latitude}, {report.inspection_longitude}" if report.inspection_latitude is not None else "Not recorded"),
        ("Evidence references", ", ".join(json.loads(report.evidence_refs_json or "[]"))),
        ("Officer declaration", "Confirmed" if report.declaration else "Not confirmed"),
    ]
    for title, value in fields:
        story.append(KeepTogether([Paragraph(escape(title), styles["Heading4"]),
                                   Paragraph(escape(str(value or "Not provided")).replace("\n", "<br/>"), styles["SGValue"])]))
    doc.build(story)
    return buf.getvalue()

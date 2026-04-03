from __future__ import annotations

import os
from datetime import datetime, timedelta
from textwrap import wrap
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from .models import (
    AuditLog,
    Check,
    Event,
    Incident,
    IncidentNote,
    OnCallAlert,
    Project,
    RemediationApproval,
    RemediationLog,
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _format_timestamp(value: Optional[datetime]) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else "n/a"


def _format_duration(started_at: datetime, resolved_at: Optional[datetime]) -> str:
    end = resolved_at or datetime.utcnow()
    total_seconds = max(0, int((end - started_at).total_seconds()))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _base_url() -> str:
    return (os.environ.get("BASE_URL") or "").rstrip("/")


def _absolute_or_relative(path: str) -> str:
    base = _base_url()
    return f"{base}{path}" if base else path


def _timeline_item(
    *,
    ts: datetime,
    kind: str,
    title: str,
    summary: str,
    actor: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    return {
        "ts": _iso(ts),
        "sort_ts": ts,
        "kind": kind,
        "title": title,
        "summary": summary,
        "actor": actor,
        "metadata": metadata or {},
    }


def _event_title(event_type: str) -> str:
    labels = {
        "down": "Check reported DOWN",
        "up": "Check recovered",
        "degraded": "Check reported DEGRADED",
        "http_failure": "HTTP failure recorded",
        "heartbeat": "Heartbeat recorded",
    }
    return labels.get(event_type, f"Event: {event_type}")


def _audit_title(action: str) -> str:
    labels = {
        "assign_incident": "Incident assigned",
        "ack_incident": "Incident acknowledged",
        "clear_incident_ack": "Incident acknowledgement cleared",
        "silence_incident": "Incident silenced",
        "clear_incident_silence": "Incident silence cleared",
        "merge_incident": "Incident merged",
        "split_incident": "Incident split",
        "create_share": "Share link created",
        "pagerduty_ack": "PagerDuty acknowledgement synced",
        "pagerduty_clear_ack": "PagerDuty acknowledgement cleared",
        "pagerduty_resolve": "PagerDuty resolution synced",
        "pagerduty_reopen": "PagerDuty reopen synced",
        "pagerduty_assign": "PagerDuty assignment synced",
        "pagerduty_note": "PagerDuty note synced",
    }
    return labels.get(action, action.replace("_", " ").title())


def _incident_window(incident: Incident) -> tuple[datetime, datetime]:
    started_at = incident.started_at
    resolved_at = incident.resolved_at or datetime.utcnow()
    return started_at - timedelta(minutes=1), resolved_at + timedelta(minutes=5)


def _build_postmortem_links(incident: Incident) -> dict:
    status_page_path = f"/ui/status/{incident.project_id}"
    links = {
        "status_page_path": status_page_path,
        "status_page_url": _absolute_or_relative(status_page_path),
        "public_incident_path": None,
        "public_incident_url": None,
    }
    if incident.share_token:
        public_incident_path = f"/ui/incidents/public/{incident.share_token}"
        links["public_incident_path"] = public_incident_path
        links["public_incident_url"] = _absolute_or_relative(public_incident_path)
    return links


def _timeline_titles(payload: dict, kind: str) -> list[str]:
    return [item["title"] for item in payload["timeline"] if item.get("kind") == kind]


def _evidence_summary(payload: dict) -> list[str]:
    evidence: list[str] = []
    if payload["timeline"]:
        first = payload["timeline"][0]
        evidence.append(f"First recorded signal: {first['title']} at {first['ts']}.")
        if len(payload["timeline"]) > 1:
            latest = payload["timeline"][-1]
            evidence.append(f"Latest recorded signal: {latest['title']} at {latest['ts']}.")
    if payload["stats"]["alerts"]:
        evidence.append(f"On-call alert fan-out occurred {payload['stats']['alerts']} time(s).")
    if payload["stats"]["remediation_steps"]:
        evidence.append(f"Remediation workflow recorded {payload['stats']['remediation_steps']} step(s).")
    if payload["stats"]["notes"]:
        evidence.append(f"Responders captured {payload['stats']['notes']} investigation note(s).")
    return evidence


def _response_summary(payload: dict, incident: Incident) -> list[str]:
    summary: list[str] = []
    if incident.owner:
        summary.append(f"Incident owner: {incident.owner}.")
    if incident.acknowledged_at:
        summary.append(
            f"Acknowledged at {_format_timestamp(incident.acknowledged_at)} by {incident.acknowledged_by or 'unknown'}."
        )
    alert_titles = _timeline_titles(payload, "alert")
    if alert_titles:
        summary.append(f"Alerting recorded: {', '.join(alert_titles[:2])}.")
    workflow_titles = _timeline_titles(payload, "workflow")
    if workflow_titles:
        summary.append(f"Workflow updates: {', '.join(workflow_titles[:3])}.")
    remediation_titles = _timeline_titles(payload, "remediation")
    if remediation_titles:
        summary.append(f"Remediation activity: {', '.join(remediation_titles[:3])}.")
    return summary


def _action_items(payload: dict) -> list[str]:
    check_label = payload["check_name"] or f"check {payload['check_id']}"
    items = [
        f"Confirm the permanent fix for {check_label} and record the specific technical root cause.",
        f"Update the runbook for {check_label} with the validated investigation and recovery steps from this incident.",
    ]
    if payload["stats"]["alerts"]:
        items.append(f"Review alert routing, dedupe, and escalation timing for {check_label}.")
    else:
        items.append(f"Add or validate alert coverage for {check_label} so similar failures are detected faster.")
    if payload["stats"]["notes"]:
        items.append("Convert the captured investigation notes into a concise operator checklist and handoff note.")
    else:
        items.append("Add a responder note template so future incidents preserve investigation context.")
    return items[:4]


def build_incident_timeline(session: Session, incident: Incident) -> dict:
    project = session.get(Project, incident.project_id)
    check = session.get(Check, incident.check_id)
    window_start, window_end = _incident_window(incident)
    items: list[dict] = []

    items.append(
        _timeline_item(
            ts=incident.started_at,
            kind="incident",
            title="Incident opened",
            summary=f"Incident {incident.id} opened for check {check.name if check else incident.check_id}.",
        )
    )
    if incident.resolved_at:
        items.append(
            _timeline_item(
                ts=incident.resolved_at,
                kind="incident",
                title="Incident resolved",
                summary=f"Incident {incident.id} resolved after {_format_duration(incident.started_at, incident.resolved_at)}.",
            )
        )

    events = session.exec(
        select(Event).where(Event.incident_id == incident.id).order_by(Event.created_at.asc())
    ).all()
    for event in events:
        items.append(
            _timeline_item(
                ts=event.created_at,
                kind="event",
                title=_event_title(event.event_type),
                summary=event.message or event.event_type,
                metadata={"event_id": event.id, "event_type": event.event_type},
            )
        )

    notes = session.exec(
        select(IncidentNote).where(IncidentNote.incident_id == incident.id).order_by(IncidentNote.created_at.asc())
    ).all()
    for note in notes:
        items.append(
            _timeline_item(
                ts=note.created_at,
                kind="note",
                title="Investigation note added",
                summary=note.body,
                actor=note.author,
                metadata={"note_id": note.id},
            )
        )

    audit_logs = session.exec(
        select(AuditLog)
        .where(
            or_(
                (AuditLog.target_type == "incident") & (AuditLog.target_id == incident.id),
                (AuditLog.action == "merge_incident") & AuditLog.details.contains(f"merged_into={incident.id}"),
                (AuditLog.action == "split_incident") & AuditLog.details.contains(f"split_into={incident.id}"),
            )
        )
        .order_by(AuditLog.created_at.asc())
    ).all()
    for log in audit_logs:
        if log.action == "note_incident":
            continue
        items.append(
            _timeline_item(
                ts=log.created_at,
                kind="workflow",
                title=_audit_title(log.action),
                summary=log.details or log.action,
                actor=log.actor,
                metadata={"audit_id": log.id, "action": log.action},
            )
        )

    oncall_alerts = session.exec(
        select(OnCallAlert)
        .where(
            OnCallAlert.project_id == incident.project_id,
            OnCallAlert.check_id == incident.check_id,
            OnCallAlert.created_at >= window_start,
            OnCallAlert.created_at <= window_end,
        )
        .order_by(OnCallAlert.created_at.asc())
    ).all()
    for alert in oncall_alerts:
        items.append(
            _timeline_item(
                ts=alert.created_at,
                kind="alert",
                title="On-call alert opened",
                summary=alert.message or f"{alert.event_type} alert opened at escalation level {alert.escalation_level}.",
                metadata={"oncall_alert_id": alert.id, "status": alert.status},
            )
        )
        if alert.last_notified_at and alert.last_notified_at != alert.created_at:
            items.append(
                _timeline_item(
                    ts=alert.last_notified_at,
                    kind="alert",
                    title="On-call notification sent",
                    summary=f"Escalation level {alert.escalation_level} notification sent.",
                    metadata={"oncall_alert_id": alert.id},
                )
            )

    remediation_logs = session.exec(
        select(RemediationLog)
        .where(
            RemediationLog.project_id == incident.project_id,
            RemediationLog.check_id == incident.check_id,
            RemediationLog.created_at >= window_start,
            RemediationLog.created_at <= window_end,
        )
        .order_by(RemediationLog.created_at.asc())
    ).all()
    for log in remediation_logs:
        detail = log.message or f"status={log.status}"
        if log.response_code is not None:
            detail = f"{detail} (response={log.response_code})"
        items.append(
            _timeline_item(
                ts=log.created_at,
                kind="remediation",
                title="Remediation step executed",
                summary=detail,
                metadata={"remediation_log_id": log.id, "status": log.status},
            )
        )

    approvals = session.exec(
        select(RemediationApproval)
        .where(
            RemediationApproval.project_id == incident.project_id,
            RemediationApproval.check_id == incident.check_id,
            RemediationApproval.requested_at >= window_start,
            RemediationApproval.requested_at <= window_end,
        )
        .order_by(RemediationApproval.requested_at.asc())
    ).all()
    for approval in approvals:
        items.append(
            _timeline_item(
                ts=approval.requested_at,
                kind="remediation",
                title="Remediation approval requested",
                summary=approval.reason or approval.event_type,
                metadata={"approval_id": approval.id, "status": approval.status},
            )
        )
        if approval.decided_at:
            items.append(
                _timeline_item(
                    ts=approval.decided_at,
                    kind="remediation",
                    title=f"Remediation {approval.status}",
                    summary=f"Decision by {approval.decided_by or 'unknown'}.",
                    actor=approval.decided_by,
                    metadata={"approval_id": approval.id, "status": approval.status},
                )
            )
        if approval.executed_at:
            items.append(
                _timeline_item(
                    ts=approval.executed_at,
                    kind="remediation",
                    title="Approved remediation executed",
                    summary=approval.execution_message or approval.execution_status or "execution complete",
                    metadata={"approval_id": approval.id, "execution_status": approval.execution_status},
                )
            )

    items.sort(key=lambda item: (item["sort_ts"], item["kind"], item["title"]))
    for item in items:
        item.pop("sort_ts", None)

    return {
        "incident_id": incident.id,
        "project_id": incident.project_id,
        "project_name": project.name if project else None,
        "check_id": incident.check_id,
        "check_name": check.name if check else None,
        "status": incident.status,
        "started_at": _iso(incident.started_at),
        "resolved_at": _iso(incident.resolved_at),
        "duration": _format_duration(incident.started_at, incident.resolved_at),
        "timeline": items,
        "stats": {
            "events": len(events),
            "notes": len(notes),
            "alerts": len(oncall_alerts),
            "remediation_steps": len(remediation_logs) + len(approvals),
        },
        "links": _build_postmortem_links(incident),
    }


def render_incident_postmortem_markdown(session: Session, incident: Incident) -> str:
    payload = build_incident_timeline(session, incident)
    links = payload["links"]
    evidence_summary = _evidence_summary(payload)
    response_summary = _response_summary(payload, incident)
    action_items = _action_items(payload)
    lines = [
        f"# Incident Postmortem: Incident {payload['incident_id']}",
        "",
        "## Executive Summary",
        "",
        f"- Project: {payload['project_name'] or payload['project_id']}",
        f"- Check: {payload['check_name'] or payload['check_id']}",
        f"- Status: {payload['status']}",
        f"- Started: {_format_timestamp(incident.started_at)}",
        f"- Resolved: {_format_timestamp(incident.resolved_at)}",
        f"- Duration: {payload['duration']}",
        f"- Event count: {payload['stats']['events']}",
        f"- Notes: {payload['stats']['notes']}",
        f"- On-call alerts: {payload['stats']['alerts']}",
        f"- Remediation entries: {payload['stats']['remediation_steps']}",
        f"- Status page: {links['status_page_url']}",
        f"- Shared incident page: {links['public_incident_url'] or 'Create a share link if external access is needed.'}",
        "",
        "## Root Cause",
        "",
        "- Primary cause: _Fill in the direct technical cause._",
        "- Contributing factors: _Fill in contributing conditions, regressions, or dependencies._",
        "- Evidence captured:",
        "",
    ]

    if evidence_summary:
        lines.extend([f"  - {item}" for item in evidence_summary])
    else:
        lines.append("  - _Add the strongest signals that support the root-cause conclusion._")

    lines.extend(
        [
            "",
            "## Impact",
            "",
            f"- Customer/system impact window: {payload['duration']}",
            "- Affected users or components: _Describe what customers or internal systems experienced._",
            "- Severity assessment: _Document the business or operational severity._",
            "",
            "## Detection & Response",
            "",
        ]
    )

    if response_summary:
        lines.extend([f"- {item}" for item in response_summary])
    else:
        lines.append("- _Describe how the issue was detected and who responded first._")

    lines.extend(
        [
            "",
            "## Customer Communication",
            "",
            f"- Public status page: {links['status_page_url']}",
            f"- Shared incident page: {links['public_incident_url'] or 'Not created'}",
            "- Customer-facing summary: _Capture the message sent to the status page, support, or stakeholders._",
            "",
            "## Timeline",
            "",
        ]
    )

    if not payload["timeline"]:
        lines.append("- No timeline entries recorded.")
    else:
        for item in payload["timeline"]:
            actor = f" ({item['actor']})" if item.get("actor") else ""
            lines.append(f"- `{item['ts']}` [{item['kind']}] **{item['title']}**{actor}: {item['summary']}")

    lines.extend(
        [
            "",
            "## Action Items",
            "",
            "| Action Item | Owner | Due | Status |",
            "| --- | --- | --- | --- |",
            "",
        ]
    )
    for item in action_items:
        lines.append(f"| {item} | _unassigned_ | _YYYY-MM-DD_ | open |")
    lines.append("")
    return "\n".join(lines)


def _escape_pdf_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _build_simple_pdf(title: str, lines: list[str]) -> bytes:
    wrapped_lines: list[str] = []
    for line in [title, ""] + lines:
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(wrap(line, width=92) or [""])

    lines_per_page = 48
    pages: list[list[str]] = [
        wrapped_lines[i : i + lines_per_page] for i in range(0, len(wrapped_lines), lines_per_page)
    ] or [["No content"]]

    objects: list[bytes] = [b"", b""]
    font_id = 3
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []

    for page_lines in pages:
        content_parts = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for index, line in enumerate(page_lines):
            if index > 0:
                content_parts.append("T*")
            content_parts.append(f"({_escape_pdf_text(line)}) Tj")
        content_parts.append("ET")
        stream = "\n".join(content_parts).encode("latin-1", "replace")
        content_id = len(objects) + 1
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )
        page_id = len(objects) + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(
                "latin-1"
            )
        )
        page_ids.append(page_id)

    objects[1] = f"<< /Type /Catalog /Pages 2 0 R >>".encode("latin-1")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj_id, body in enumerate(objects[1:], start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{obj_id} 0 obj\n".encode("latin-1"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects)}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)


def render_incident_postmortem_pdf(session: Session, incident: Incident) -> bytes:
    markdown = render_incident_postmortem_markdown(session, incident)
    title = f"Incident {incident.id} Postmortem"
    return _build_simple_pdf(title, markdown.splitlines())

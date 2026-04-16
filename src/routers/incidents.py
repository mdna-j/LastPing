import json
import os
from pathlib import Path as FilePath
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, Path, Body
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import constr, root_validator
from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from ..alerts import notify_incident_pagerduty_update, notify_incident_slack_update
from ..db import get_session
from ..deps import (
    get_audit_context,
    limit_public_requests,
    limit_integration_action_requests,
    require_admin_or_owner,
    require_project_access,
)
from ..models import AuditLog, BrowserCheckArtifact, Check, Event, Incident, IncidentNote, NotificationDelivery, Project
from ..notification_queue import STATUS_PROCESSING, STATUS_QUEUED, STATUS_RETRY, queue_jira_ticket_delivery
from ..postmortems import (
    build_incident_timeline,
    render_incident_postmortem_markdown,
    render_incident_postmortem_pdf,
)
from ..schemas import StrictBaseModel
from ..secret_lifecycle import SECRET_JIRA_API_TOKEN, active_project_secret_candidates

router = APIRouter(prefix="/projects/{project_id}", tags=["incidents"])


def _serialize_note(note: IncidentNote) -> dict:
    return {
        "id": note.id,
        "incident_id": note.incident_id,
        "project_id": note.project_id,
        "author": note.author,
        "body": note.body,
        "created_at": note.created_at.isoformat(),
    }


def _artifact_root() -> FilePath:
    base = FilePath(
        os.environ.get("BROWSER_CHECK_ARTIFACT_DIR")
        or os.path.join(os.getcwd(), "artifacts", "browser_checks")
    )
    return base.resolve()


def _artifact_path_allowed(path: FilePath) -> bool:
    try:
        path.resolve().relative_to(_artifact_root())
        return True
    except Exception:
        return False


def _serialize_browser_artifact(project_id: int, incident_id: int, artifact: BrowserCheckArtifact) -> dict:
    file_path = FilePath(artifact.file_path)
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "created_at": artifact.created_at.isoformat(),
        "check_id": artifact.check_id,
        "check_result_id": artifact.check_result_id,
        "file_name": file_path.name,
        "download_url": f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact.id}",
        "view_url": f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact.id}/view",
    }


def _artifact_download_url(project_id: int, incident_id: int, artifact: BrowserCheckArtifact) -> str:
    return f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact.id}"


def _har_preview_payload(raw: dict[str, Any]) -> dict[str, Any]:
    log = raw.get("log") if isinstance(raw, dict) else {}
    pages = log.get("pages") if isinstance(log, dict) else []
    entries = log.get("entries") if isinstance(log, dict) else []
    items = []
    failures = 0
    total_time_ms = 0.0
    for entry in entries[:150]:
        request = entry.get("request") if isinstance(entry, dict) else {}
        response = entry.get("response") if isinstance(entry, dict) else {}
        timing = entry.get("time") if isinstance(entry, dict) else None
        status = int(response.get("status") or 0) if isinstance(response, dict) else 0
        if status >= 400:
            failures += 1
        if timing is not None:
            try:
                total_time_ms += max(float(timing), 0.0)
            except Exception:
                pass
        items.append(
            {
                "method": request.get("method"),
                "url": request.get("url"),
                "status": status or None,
                "mime_type": response.get("content", {}).get("mimeType") if isinstance(response, dict) else None,
                "time_ms": timing,
            }
        )
    return {
        "pages": len(pages) if isinstance(pages, list) else 0,
        "entry_count": len(entries) if isinstance(entries, list) else 0,
        "error_count": failures,
        "total_time_ms": round(total_time_ms, 3),
        "requests": items,
    }


def _report_preview_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "failure_reason": raw.get("failure_reason"),
        "start_url": raw.get("start_url"),
        "final_url": raw.get("final_url"),
        "page_title": raw.get("page_title"),
        "attempt": raw.get("attempt"),
        "step_results": raw.get("step_results") or [],
        "console": raw.get("console") or [],
        "page_errors": raw.get("page_errors") or [],
        "network_failures": raw.get("network_failures") or [],
        "http_errors": raw.get("http_errors") or [],
    }


def _load_artifact_preview(path: FilePath, artifact: BrowserCheckArtifact) -> dict[str, Any]:
    artifact_type = (artifact.artifact_type or "").lower()
    content_type = (artifact.content_type or "").lower()
    if content_type.startswith("image/"):
        return {"mode": "image"}
    if content_type.startswith("video/"):
        return {"mode": "video"}
    if artifact_type == "trace" or path.suffix.lower() == ".zip":
        return {
            "mode": "trace",
            "summary": {
                "message": "Download this trace and open it with `playwright show-trace` for full replay.",
                "open_command": "playwright show-trace trace.zip",
            },
        }

    if artifact_type in {"har", "report"} or content_type.startswith("application/json") or path.suffix.lower() == ".json":
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(raw_text)
        if artifact_type == "har":
            return {"mode": "har", "summary": _har_preview_payload(parsed)}
        if artifact_type == "report":
            return {"mode": "report", "summary": _report_preview_payload(parsed), "raw_json": parsed}
        return {"mode": "json", "raw_json": parsed}

    if content_type.startswith("text/") or path.suffix.lower() in {".log", ".txt"}:
        return {
            "mode": "text",
            "text": path.read_text(encoding="utf-8", errors="replace")[:200000],
        }

    return {"mode": "download_only"}


def _normalize_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _serialize_incident(incident: Incident, note_count: int = 0) -> dict:
    return {
        "id": incident.id,
        "check_id": incident.check_id,
        "started_at": incident.started_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "status": incident.status,
        "share_token": incident.share_token,
        "group_id": incident.group_id,
        "merged_into": incident.merged_into,
        "owner": incident.owner,
        "acknowledged_at": incident.acknowledged_at.isoformat() if incident.acknowledged_at else None,
        "acknowledged_by": incident.acknowledged_by,
        "resolved_by": incident.resolved_by,
        "resolution_summary": incident.resolution_summary,
        "silenced_until": incident.silenced_until.isoformat() if incident.silenced_until else None,
        "silenced_by": incident.silenced_by,
        "jira_issue_key": incident.jira_issue_key,
        "jira_issue_url": incident.jira_issue_url,
        "note_count": note_count,
    }


def _get_incident_or_404(session: Session, project_id: int, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if not incident or incident.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def _audit(
    session: Session,
    request: Optional[Request],
    authorization: Optional[str],
    x_admin_token: Optional[str],
    action: str,
    target_id: int,
    details: str,
) -> str:
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            target_type="incident",
            target_id=target_id,
            details=details,
            actor_ip=actor_ip,
            user_agent=user_agent,
        )
    )
    return actor


def _notify_slack_thread_update(
    session: Session,
    project: Project,
    incident: Incident,
    *,
    action: str,
    body: str,
) -> None:
    try:
        check = session.get(Check, incident.check_id) if getattr(incident, "check_id", None) else None
        share_url = None
        if incident.share_token:
            base_url = os.environ.get("BASE_URL", "").rstrip("/")
            share_url = f"{base_url}/ui/incidents/public/{incident.share_token}" if base_url else f"/ui/incidents/public/{incident.share_token}"
        notify_incident_slack_update(
            project,
            incident,
            action=action,
            body=body,
            check=check,
            session=session,
            share_url=share_url,
        )
        if session.new or session.dirty or session.deleted:
            session.commit()
    except Exception:
        session.rollback()
        pass


def _notify_pagerduty_ack(
    session: Session,
    project: Project,
    incident: Incident,
    *,
    actor: Optional[str],
) -> None:
    try:
        check = session.get(Check, incident.check_id) if getattr(incident, "check_id", None) else None
        if not getattr(incident, "pagerduty_dedup_key", None):
            return
        notify_incident_pagerduty_update(
            project,
            incident,
            event_action="acknowledge",
            summary=f"Incident #{incident.id} acknowledged in LastPing",
            check=check,
            session=session,
            severity="critical",
            custom_details={
                "incident_id": incident.id,
                "project_id": getattr(project, "id", None),
                "check_id": getattr(check, "id", None) if check is not None else None,
                "actor": actor,
            },
        )
        if session.new or session.dirty or session.deleted:
            session.commit()
    except Exception:
        session.rollback()
        pass


def _incident_jira_description(session: Session, project: Project, incident: Incident) -> str:
    timeline = build_incident_timeline(session, incident)
    check = session.get(Check, incident.check_id)
    lines = [
        f"LastPing incident {incident.id} for project {project.name}.",
        f"Check: {check.name if check else incident.check_id}",
        f"Status: {incident.status}",
        f"Started: {incident.started_at.isoformat()}",
    ]
    if incident.owner:
        lines.append(f"Owner: {incident.owner}")
    if incident.acknowledged_at:
        lines.append(f"Acknowledged: {incident.acknowledged_at.isoformat()} by {incident.acknowledged_by or 'unknown'}")
    if timeline["links"].get("status_page_url"):
        lines.append(f"Status page: {timeline['links']['status_page_url']}")
    if timeline["links"].get("public_incident_url"):
        lines.append(f"Shared incident: {timeline['links']['public_incident_url']}")
    lines.append("")
    lines.append("Timeline:")
    for item in timeline["timeline"][:8]:
        summary = item.get("summary") or item.get("title") or item.get("kind") or "timeline entry"
        lines.append(f"- {item.get('ts')}: {summary}")
    return "\n".join(lines)


@router.get("/incidents")
def list_incidents(
    project_id: int = Path(..., ge=1),
    status: Optional[str] = Query(None, max_length=32),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    stmt = select(Incident).where(Incident.project_id == project_id)
    if status:
        stmt = stmt.where(Incident.status == status)
    incidents = session.exec(stmt.order_by(Incident.started_at.desc())).all()

    note_counts = {}
    incident_ids = [incident.id for incident in incidents if incident.id is not None]
    if incident_ids:
        notes = session.exec(
            select(IncidentNote).where(IncidentNote.incident_id.in_(incident_ids))
        ).all()
        for note in notes:
            note_counts[note.incident_id] = note_counts.get(note.incident_id, 0) + 1

    return [_serialize_incident(incident, note_count=note_counts.get(incident.id, 0)) for incident in incidents]


@router.get("/incidents/{incident_id}")
def get_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    events = session.exec(select(Event).where(Event.incident_id == incident_id).order_by(Event.created_at)).all()
    notes = session.exec(
        select(IncidentNote).where(IncidentNote.incident_id == incident_id).order_by(IncidentNote.created_at)
    ).all()
    artifacts = session.exec(
        select(BrowserCheckArtifact)
        .where(BrowserCheckArtifact.incident_id == incident_id)
        .order_by(BrowserCheckArtifact.created_at.desc(), BrowserCheckArtifact.id.desc())
    ).all()
    timeline = build_incident_timeline(session, incident)
    return {
        "incident": _serialize_incident(incident, note_count=len(notes)),
        "events": [
            {"id": event.id, "type": event.event_type, "message": event.message, "ts": event.created_at.isoformat()}
            for event in events
        ],
        "notes": [_serialize_note(note) for note in notes],
        "artifacts": [_serialize_browser_artifact(project_id, incident_id, artifact) for artifact in artifacts],
        "timeline": timeline["timeline"],
        "timeline_stats": timeline["stats"],
        "postmortem": {
            "duration": timeline["duration"],
            "project_name": timeline["project_name"],
            "check_name": timeline["check_name"],
        },
    }


@router.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    return build_incident_timeline(session, incident)


@router.get("/incidents/{incident_id}/artifacts/{artifact_id}")
def download_incident_artifact(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    artifact_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    artifact = session.get(BrowserCheckArtifact, artifact_id)
    if (
        artifact is None
        or artifact.project_id != project_id
        or artifact.incident_id != incident.id
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = FilePath(artifact.file_path)
    if not _artifact_path_allowed(path) or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(
        path=path,
        media_type=artifact.content_type or "application/octet-stream",
        filename=path.name,
    )


@router.get("/incidents/{incident_id}/artifacts/{artifact_id}/view")
def preview_incident_artifact(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    artifact_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    artifact = session.get(BrowserCheckArtifact, artifact_id)
    if (
        artifact is None
        or artifact.project_id != project_id
        or artifact.incident_id != incident.id
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = FilePath(artifact.file_path)
    if not _artifact_path_allowed(path) or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found")

    preview = _load_artifact_preview(path, artifact)
    payload = {
        "artifact": _serialize_browser_artifact(project_id, incident_id, artifact),
        "mode": preview.get("mode", "download_only"),
        "download_url": _artifact_download_url(project_id, incident_id, artifact),
    }
    if "summary" in preview:
        payload["summary"] = preview["summary"]
    if "raw_json" in preview:
        payload["raw_json"] = preview["raw_json"]
    if "text" in preview:
        payload["text"] = preview["text"]
    return payload


@router.get("/incidents/{incident_id}/postmortem.md", response_class=PlainTextResponse)
def export_incident_postmortem_markdown(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    markdown = render_incident_postmortem_markdown(session, incident)
    filename = f"incident-{incident.id}-postmortem.md"
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/incidents/{incident_id}/postmortem.pdf")
def export_incident_postmortem_pdf(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    payload = render_incident_postmortem_pdf(session, incident)
    filename = f"incident-{incident.id}-postmortem.pdf"
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


public_router = APIRouter(prefix="/incidents", tags=["incidents-public"], dependencies=[Depends(limit_public_requests)])


@public_router.get("/public/{token}")
def public_incident(token: constr(min_length=10, max_length=128), session: Session = Depends(get_session)):
    incident = session.exec(select(Incident).where(Incident.share_token == token)).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    events = session.exec(select(Event).where(Event.incident_id == incident.id).order_by(Event.created_at)).all()
    timeline = build_incident_timeline(session, incident)
    return {
        "project": {
            "id": timeline["project_id"],
            "name": timeline["project_name"],
            "status_page_url": timeline["links"]["status_page_url"],
        },
        "incident": {
            "id": incident.id,
            "check_id": incident.check_id,
            "check_name": timeline["check_name"],
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "status": incident.status,
            "duration": timeline["duration"],
            "share_url": timeline["links"]["public_incident_url"],
        },
        "events": [
            {"id": event.id, "type": event.event_type, "message": event.message, "ts": event.created_at.isoformat()}
            for event in events
        ],
        "timeline": timeline["timeline"],
        "timeline_stats": timeline["stats"],
    }


@router.post("/incidents/{incident_id}/share")
def create_share(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    if not incident.share_token:
        import secrets

        incident.share_token = secrets.token_urlsafe(16)
        session.add(incident)
        _audit(
            session,
            request,
            authorization,
            x_admin_token,
            "create_share",
            incident.id,
            f"share_token_created={bool(incident.share_token)}",
        )
        session.commit()
        _notify_slack_thread_update(
            session,
            _proj,
            incident,
            action="share",
            body="Created a public share link for this incident.",
        )
    return {"share_token": incident.share_token}


@router.post("/incidents/{incident_id}/jira-ticket")
def create_incident_jira_ticket(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    request: Request = None,
    _scope = Depends(limit_integration_action_requests),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    if incident.jira_issue_key and incident.jira_issue_url:
        return {
            "created": False,
            "queued": False,
            "issue_key": incident.jira_issue_key,
            "issue_url": incident.jira_issue_url,
            "incident": _serialize_incident(incident),
        }
    jira_token_candidates = active_project_secret_candidates(_proj, SECRET_JIRA_API_TOKEN, session=session)
    if not (_proj.jira_base_url and _proj.jira_user_email and _proj.jira_project_key and jira_token_candidates):
        raise HTTPException(status_code=400, detail="Jira settings are incomplete for this project")
    existing_delivery = session.exec(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.project_id == project_id,
            NotificationDelivery.incident_id == incident_id,
            NotificationDelivery.request_kind == "jira_ticket",
            NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY, STATUS_PROCESSING]),
        )
        .order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
    ).first()
    if existing_delivery is not None:
        return {
            "created": False,
            "queued": True,
            "message": "Jira ticket creation is already queued for this incident.",
            "delivery_id": existing_delivery.id,
            "issue_key": None,
            "issue_url": None,
            "incident": _serialize_incident(incident),
        }

    check = session.get(Check, incident.check_id)
    summary = f"[LastPing] Incident #{incident.id}: {(check.name if check else f'check {incident.check_id}')} {incident.status}"
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    delivery = queue_jira_ticket_delivery(
        session,
        project_id=project_id,
        check_id=getattr(check, "id", None),
        incident_id=incident.id,
        summary=summary,
        description=_incident_jira_description(session, _proj, incident),
        labels=["lastping", "incident"],
        issue_type=_proj.jira_issue_type or "Task",
        target=_proj.jira_project_key or "jira project",
        audit_actor=actor,
        audit_actor_ip=actor_ip,
        audit_user_agent=user_agent,
    )
    session.add(
        AuditLog(
            actor=actor,
            action="queue_jira_ticket",
            target_type="incident",
            target_id=incident.id,
            project_id=incident.project_id,
            details=f"delivery_id={delivery.id}",
            actor_ip=actor_ip,
            user_agent=user_agent,
        )
    )
    session.commit()
    session.refresh(incident)
    return {
        "created": False,
        "queued": True,
        "message": "Jira ticket creation queued for background delivery.",
        "delivery_id": delivery.id,
        "issue_key": None,
        "issue_url": None,
        "incident": _serialize_incident(incident),
    }


class IncidentAssignPayload(StrictBaseModel):
    owner: Optional[constr(max_length=255)] = None


@router.post("/incidents/{incident_id}/assign")
def assign_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentAssignPayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    previous_owner = incident.owner
    incident.owner = payload.owner
    session.add(incident)
    _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "assign_incident",
        incident.id,
        f"owner:{previous_owner or '-'}->{incident.owner or '-'}",
    )
    session.commit()
    session.refresh(incident)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="assign",
        body=f"Assigned incident owner to `{incident.owner or 'unassigned'}`.",
    )
    return {"incident": _serialize_incident(incident)}


class IncidentAckPayload(StrictBaseModel):
    acknowledged: bool = True


@router.post("/incidents/{incident_id}/ack")
def acknowledge_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentAckPayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    actor = _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "ack_incident" if payload.acknowledged else "clear_incident_ack",
        incident.id,
        f"acknowledged={payload.acknowledged}",
    )
    if payload.acknowledged:
        incident.acknowledged_at = datetime.utcnow()
        incident.acknowledged_by = actor
    else:
        incident.acknowledged_at = None
        incident.acknowledged_by = None
    session.add(incident)
    session.commit()
    session.refresh(incident)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="ack" if payload.acknowledged else "clear_ack",
        body=(
            f"Acknowledged by `{incident.acknowledged_by}`."
            if payload.acknowledged
            else "Cleared the incident acknowledgement."
        ),
    )
    if payload.acknowledged:
        _notify_pagerduty_ack(session, _proj, incident, actor=incident.acknowledged_by)
    return {"incident": _serialize_incident(incident)}


class IncidentSilencePayload(StrictBaseModel):
    until: Optional[datetime] = None
    clear: bool = False

    @root_validator
    def validate_choice(cls, values):
        until = values.get("until")
        clear = values.get("clear")
        if clear and until is not None:
            raise ValueError("Provide either clear=true or until, not both")
        if not clear and until is None:
            raise ValueError("Provide a silence deadline or clear=true")
        return values


@router.post("/incidents/{incident_id}/silence")
def silence_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentSilencePayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    actor = _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "silence_incident" if not payload.clear else "clear_incident_silence",
        incident.id,
        f"silenced_until={payload.until.isoformat() if payload.until else '-'} clear={payload.clear}",
    )
    if payload.clear:
        incident.silenced_until = None
        incident.silenced_by = None
    else:
        silenced_until = _normalize_utc_naive(payload.until)
        if silenced_until <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="Silence deadline must be in the future")
        incident.silenced_until = silenced_until
        incident.silenced_by = actor
    session.add(incident)
    session.commit()
    session.refresh(incident)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="silence" if not payload.clear else "clear_silence",
        body=(
            f"Silenced notifications until `{incident.silenced_until.isoformat()}`."
            if incident.silenced_until
            else "Cleared the incident silence window."
        ),
    )
    return {"incident": _serialize_incident(incident)}


class IncidentResolvePayload(StrictBaseModel):
    summary: constr(min_length=3, max_length=4000)


class IncidentReopenPayload(StrictBaseModel):
    reason: Optional[constr(max_length=1000)] = None


class IncidentNotePayload(StrictBaseModel):
    body: constr(max_length=4000)


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentResolvePayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    if incident.status == "merged":
        raise HTTPException(status_code=400, detail="Merged incidents cannot be manually resolved")
    actor = _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "resolve_incident",
        incident.id,
        f"summary={payload.summary.strip()}",
    )
    incident.resolved_at = datetime.utcnow()
    incident.status = "resolved"
    incident.resolved_by = actor
    incident.resolution_summary = payload.summary.strip()
    incident.silenced_until = None
    incident.silenced_by = None
    session.add(incident)
    session.commit()
    session.refresh(incident)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="resolve",
        body=f"Resolved incident with summary:\n>{incident.resolution_summary}",
    )
    try:
        check = session.get(Check, incident.check_id) if getattr(incident, "check_id", None) else None
        notify_incident_pagerduty_update(
            _proj,
            incident,
            event_action="resolve",
            summary=f"Incident #{incident.id} resolved in LastPing",
            check=check,
            session=session,
            severity="info",
            custom_details={
                "incident_id": incident.id,
                "project_id": project_id,
                "check_id": getattr(check, "id", None) if check is not None else None,
                "actor": actor,
                "resolution_summary": incident.resolution_summary,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
    return {"incident": _serialize_incident(incident)}


@router.post("/incidents/{incident_id}/reopen")
def reopen_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentReopenPayload = Body(default=IncidentReopenPayload()),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    if incident.status == "merged":
        raise HTTPException(status_code=400, detail="Merged incidents cannot be reopened")
    reason = (payload.reason or "").strip()
    actor = _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "reopen_incident",
        incident.id,
        f"reason={reason or '-'}",
    )
    incident.status = "open"
    incident.resolved_at = None
    incident.resolved_by = None
    incident.resolution_summary = None
    session.add(incident)
    session.commit()
    session.refresh(incident)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="reopen",
        body=(
            f"Reopened incident.{f' Reason: {reason}' if reason else ''}"
        ),
    )
    try:
        check = session.get(Check, incident.check_id) if getattr(incident, "check_id", None) else None
        notify_incident_pagerduty_update(
            _proj,
            incident,
            event_action="trigger",
            summary=f"Incident #{incident.id} reopened in LastPing",
            check=check,
            session=session,
            severity="critical",
            custom_details={
                "incident_id": incident.id,
                "project_id": project_id,
                "check_id": getattr(check, "id", None) if check is not None else None,
                "actor": actor,
                "reopen_reason": reason or None,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
    return {"incident": _serialize_incident(incident)}


@router.post("/incidents/{incident_id}/notes")
def add_incident_note(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: IncidentNotePayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    actor = _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "note_incident",
        incident.id,
        f"body_len={len(payload.body)}",
    )
    note = IncidentNote(
        incident_id=incident.id,
        project_id=project_id,
        author=actor,
        body=payload.body,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    _notify_slack_thread_update(
        session,
        _proj,
        incident,
        action="note",
        body=f"*{note.author or 'operator'}* added a note:\n>{note.body}",
    )
    return {"note": _serialize_note(note)}


class MergePayload(StrictBaseModel):
    into: int


@router.post("/incidents/{incident_id}/merge")
def merge_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: MergePayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    src = _get_incident_or_404(session, project_id, incident_id)
    tgt = _get_incident_or_404(session, project_id, payload.into)
    if src.id == tgt.id:
        raise HTTPException(status_code=400, detail="Cannot merge an incident into itself")
    events = session.exec(select(Event).where(Event.incident_id == src.id)).all()
    for event in events:
        event.incident_id = tgt.id
        session.add(event)
    src.merged_into = tgt.id
    src.group_id = tgt.id
    src.resolved_at = datetime.utcnow()
    src.status = "merged"
    session.add(src)
    _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "merge_incident",
        src.id,
        f"merged_into={tgt.id}",
    )
    session.commit()
    return {"merged": True, "into": tgt.id}


class SplitPayload(StrictBaseModel):
    event_ids: list[int]


@router.post("/incidents/{incident_id}/split")
def split_incident(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    payload: SplitPayload = Body(...),
    request: Request = None,
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_admin_or_owner),
    x_admin_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    src = _get_incident_or_404(session, project_id, incident_id)
    if not payload.event_ids:
        raise HTTPException(status_code=400, detail="No event IDs provided to split")
    events = session.exec(
        select(Event).where(Event.id.in_(payload.event_ids), Event.incident_id == src.id)
    ).all()
    if not events:
        raise HTTPException(status_code=400, detail="No matching events found to split")

    new_incident = Incident(project_id=src.project_id, check_id=src.check_id, started_at=events[0].created_at, status="open")
    session.add(new_incident)
    session.commit()
    session.refresh(new_incident)
    try:
        session.exec(
            sa_update(Event.__table__)
            .where(Event.__table__.c.id.in_([event.id for event in events]))
            .values(incident_id=new_incident.id)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    _audit(
        session,
        request,
        authorization,
        x_admin_token,
        "split_incident",
        src.id,
        f"split_into={new_incident.id}, events={[event.id for event in events]}",
    )
    session.commit()
    return {"split_into": new_incident.id, "moved_events": [event.id for event in events]}

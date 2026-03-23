from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, Path, Body
from pydantic import constr, root_validator
from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from ..db import get_session
from ..deps import (
    get_audit_context,
    limit_public_requests,
    require_admin_or_owner,
    require_project_access,
)
from ..models import AuditLog, Event, Incident, IncidentNote, Project
from ..schemas import StrictBaseModel

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
        "silenced_until": incident.silenced_until.isoformat() if incident.silenced_until else None,
        "silenced_by": incident.silenced_by,
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
    return {
        "incident": _serialize_incident(incident, note_count=len(notes)),
        "events": [
            {"id": event.id, "type": event.event_type, "message": event.message, "ts": event.created_at.isoformat()}
            for event in events
        ],
        "notes": [_serialize_note(note) for note in notes],
    }


public_router = APIRouter(prefix="/incidents", tags=["incidents-public"], dependencies=[Depends(limit_public_requests)])


@public_router.get("/public/{token}")
def public_incident(token: constr(min_length=10, max_length=128), session: Session = Depends(get_session)):
    incident = session.exec(select(Incident).where(Incident.share_token == token)).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    events = session.exec(select(Event).where(Event.incident_id == incident.id).order_by(Event.created_at)).all()
    return {
        "incident": {
            "id": incident.id,
            "check_id": incident.check_id,
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "status": incident.status,
        },
        "events": [
            {"id": event.id, "type": event.event_type, "message": event.message, "ts": event.created_at.isoformat()}
            for event in events
        ],
    }


@router.post("/incidents/{incident_id}/share")
def create_share(
    project_id: int = Path(..., ge=1),
    incident_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_access),
):
    incident = _get_incident_or_404(session, project_id, incident_id)
    if not incident.share_token:
        import secrets

        incident.share_token = secrets.token_urlsafe(16)
        session.add(incident)
        session.commit()
    return {"share_token": incident.share_token}


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
    return {"incident": _serialize_incident(incident)}


class IncidentNotePayload(StrictBaseModel):
    body: constr(max_length=4000)


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

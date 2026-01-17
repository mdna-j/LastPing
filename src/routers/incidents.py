from datetime import datetime
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from sqlmodel import Session, select
from pydantic import BaseModel

from ..db import get_session
from ..models import Incident, Event, Project, AuditLog, UserToken
from ..deps import require_project_api_key, require_admin_or_owner
from sqlalchemy import update as sa_update

router = APIRouter(prefix="/projects/{project_id}", tags=["incidents"])


@router.get("/incidents")
def list_incidents(project_id: int, status: Optional[str] = Query(None), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    stmt = select(Incident).where(Incident.project_id == project_id)
    if status:
        stmt = stmt.where(Incident.status == status)
    stmt = stmt.order_by(Incident.started_at.desc())
    incs = session.exec(stmt).all()
    out = []
    for i in incs:
        out.append({
            "id": i.id,
            "check_id": i.check_id,
            "started_at": i.started_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "status": i.status,
            "share_token": i.share_token,
            "group_id": getattr(i, 'group_id', None),
            "merged_into": getattr(i, 'merged_into', None),
        })
    return out


@router.get("/incidents/{incident_id}")
def get_incident(project_id: int, incident_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    inc = session.get(Incident, incident_id)
    if not inc or inc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    # load events
    evs = session.exec(select(Event).where(Event.incident_id == incident_id).order_by(Event.created_at)).all()
    events = [{"id": e.id, "type": e.event_type, "message": e.message, "ts": e.created_at.isoformat()} for e in evs]
    return {
        "incident": {
            "id": inc.id,
            "check_id": inc.check_id,
            "started_at": inc.started_at.isoformat(),
            "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            "status": inc.status,
            "share_token": inc.share_token,
            "group_id": getattr(inc, 'group_id', None),
            "merged_into": getattr(inc, 'merged_into', None),
        },
        "events": events,
    }


# Public access via share token
public_router = APIRouter(prefix="/incidents", tags=["incidents-public"])


@public_router.get("/public/{token}")
def public_incident(token: str, session: Session = Depends(get_session)):
    inc = session.exec(select(Incident).where(Incident.share_token == token)).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    evs = session.exec(select(Event).where(Event.incident_id == inc.id).order_by(Event.created_at)).all()
    events = [{"id": e.id, "type": e.event_type, "message": e.message, "ts": e.created_at.isoformat()} for e in evs]
    return {"incident": {"id": inc.id, "check_id": inc.check_id, "started_at": inc.started_at.isoformat(), "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None, "status": inc.status}, "events": events}


@router.post("/incidents/{incident_id}/share")
def create_share(project_id: int, incident_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    inc = session.get(Incident, incident_id)
    if not inc or inc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not inc.share_token:
        import secrets

        inc.share_token = secrets.token_urlsafe(16)
        session.add(inc)
        session.commit()
    return {"share_token": inc.share_token}


class MergePayload(BaseModel):
    into: int


@router.post("/incidents/{incident_id}/merge")
def merge_incident(project_id: int, incident_id: int, payload: MergePayload, request: Request, session: Session = Depends(get_session), _proj: Project = Depends(require_admin_or_owner), x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    src = session.get(Incident, incident_id)
    tgt = session.get(Incident, payload.into)
    if not src or src.project_id != project_id:
        raise HTTPException(status_code=404, detail="Source incident not found")
    if not tgt or tgt.project_id != project_id:
        raise HTTPException(status_code=404, detail="Target incident not found")
    if src.id == tgt.id:
        raise HTTPException(status_code=400, detail="Cannot merge an incident into itself")
    # move events
    evs = session.exec(select(Event).where(Event.incident_id == src.id)).all()
    for e in evs:
        e.incident_id = tgt.id
        session.add(e)
    # mark source as merged
    src.merged_into = tgt.id
    # also set group_id to the merge target for easier queries
    src.group_id = tgt.id
    src.resolved_at = datetime.utcnow()
    src.status = "merged"
    session.add(src)
    # record audit log with actor context
    actor = 'unknown'
    actor_ip = None
    user_agent = None
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        actor = 'admin'
    elif authorization and authorization.lower().startswith('bearer '):
        tok = authorization.split(None, 1)[1].strip()
        ut = session.exec(select(UserToken).where(UserToken.token == tok)).first()
        if ut:
            actor = f"user:{ut.user_id}"
    # capture request context if available
    try:
        actor_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get('user-agent') if request else None
    except Exception:
        actor_ip = None
        user_agent = None
    al = AuditLog(actor=actor, action='merge_incident', target_type='incident', target_id=src.id, details=f"merged_into={tgt.id}", actor_ip=actor_ip, user_agent=user_agent)
    session.add(al)
    session.commit()
    return {"merged": True, "into": tgt.id}


class SplitPayload(BaseModel):
    event_ids: list[int]


@router.post("/incidents/{incident_id}/split")
def split_incident(project_id: int, incident_id: int, payload: SplitPayload, request: Request, session: Session = Depends(get_session), _proj: Project = Depends(require_admin_or_owner), x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    src = session.get(Incident, incident_id)
    if not src or src.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not payload.event_ids:
        raise HTTPException(status_code=400, detail="No event IDs provided to split")
    # load events to move
    evs = session.exec(select(Event).where(Event.id.in_(payload.event_ids), Event.incident_id == src.id)).all()
    if not evs:
        raise HTTPException(status_code=400, detail="No matching events found to split")
    # create new incident
    new_inc = Incident(project_id=src.project_id, check_id=src.check_id, started_at=evs[0].created_at, status="open")
    session.add(new_inc)
    session.commit()
    session.refresh(new_inc)
    # perform an UPDATE at SQL level to avoid potential session visibility
    try:
        # Use the underlying table to ensure the UPDATE targets rows directly
        session.exec(sa_update(Event.__table__).where(Event.__table__.c.id.in_([e.id for e in evs])).values(incident_id=new_inc.id))
        session.commit()
    except Exception:
        session.rollback()
        raise

    # record audit log for split with actor + request context
    actor = 'unknown'
    actor_ip = None
    user_agent = None
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        actor = 'admin'
    elif authorization and authorization.lower().startswith('bearer '):
        tok = authorization.split(None, 1)[1].strip()
        ut = session.exec(select(UserToken).where(UserToken.token == tok)).first()
        if ut:
            actor = f"user:{ut.user_id}"
    try:
        actor_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get('user-agent') if request else None
    except Exception:
        actor_ip = None
        user_agent = None
    al = AuditLog(actor=actor, action='split_incident', target_type='incident', target_id=src.id, details=f"split_into={new_inc.id}, events={[e.id for e in evs]}", actor_ip=actor_ip, user_agent=user_agent)
    session.add(al)
    session.commit()

    # return confirmation
    return {"split_into": new_inc.id, "moved_events": [e.id for e in evs]}

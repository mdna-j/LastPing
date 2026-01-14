from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models import Incident, Event, Project
from ..deps import require_project_api_key

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
        out.append({"id": i.id, "check_id": i.check_id, "started_at": i.started_at.isoformat(), "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None, "status": i.status, "share_token": i.share_token})
    return out


@router.get("/incidents/{incident_id}")
def get_incident(project_id: int, incident_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    inc = session.get(Incident, incident_id)
    if not inc or inc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    # load events
    evs = session.exec(select(Event).where(Event.incident_id == incident_id).order_by(Event.created_at)).all()
    events = [{"id": e.id, "type": e.event_type, "message": e.message, "ts": e.created_at.isoformat()} for e in evs]
    return {"incident": {"id": inc.id, "check_id": inc.check_id, "started_at": inc.started_at.isoformat(), "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None, "status": inc.status, "share_token": inc.share_token}, "events": events}


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

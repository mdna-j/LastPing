import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Body
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, Heartbeat as HeartbeatModel, Event as EventModel, CheckType, EventType
from ..deps import require_project_api_key


router = APIRouter(prefix="/projects/{project_id}", tags=["heartbeats"])


@router.post("/heartbeat/{name}")
def post_heartbeat(project_id: int, name: str, payload: Optional[Dict[str, Any]] = Body(None), _proj = Depends(require_project_api_key), session: Session = Depends(get_session)):
    # find check by project and name
    check = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == name)).first()

    if not check:
        # create a heartbeat check by default
        check = CheckModel(project_id=project_id, name=name, type=CheckType.HEARTBEAT)
        session.add(check)
        session.commit()
        session.refresh(check)

    # record heartbeat
    hb = HeartbeatModel(check_id=check.id, payload=json.dumps(payload) if payload is not None else None)
    session.add(hb)

    # update check state
    prev_status = check.status
    check.last_ping = datetime.utcnow()
    check.consecutive_failures = 0
    check.status = "UP"
    session.add(check)

    # if previously DOWN, emit recovery event
    if prev_status != "UP":
        event = EventModel(check_id=check.id, project_id=project_id, event_type=EventType.UP, message="Recovered via heartbeat")
        session.add(event)

    session.commit()
    session.refresh(check)
    return {"status": check.status, "last_ping": check.last_ping}

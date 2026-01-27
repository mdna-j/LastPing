import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Body, Path
from pydantic import constr
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, Heartbeat as HeartbeatModel, Event as EventModel, CheckType, EventType
from ..deps import require_project_api_key, limit_by_api_key
from ..schemas import StrictBaseModel


router = APIRouter(prefix="/projects/{project_id}", tags=["heartbeats"])


class HeartbeatIn(StrictBaseModel):
    timestamp: Optional[datetime] = None
    message: Optional[constr(max_length=500)] = None


@router.post("/heartbeat/{name}")
def post_heartbeat(project_id: int = Path(..., ge=1), name: str = Path(..., min_length=1, max_length=120), payload: Optional[HeartbeatIn] = Body(None), _proj = Depends(require_project_api_key), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    """Ingest a heartbeat for `project_id`/`name`.

    - Creates a `Check` automatically the first time a heartbeat for
      a given `name` is received.
    - Records a `Heartbeat` row and updates the parent's `last_ping`.
    - Emits a recovery `Event` if the check was previously `DOWN`.
    """
    # find check by project and name
    check = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == name)).first()

    if not check:
        # create a heartbeat check by default on first use
        check = CheckModel(project_id=project_id, name=name, type=CheckType.HEARTBEAT)
        session.add(check)
        session.commit()
        session.refresh(check)

    # record heartbeat row (payload optional)
    hb_payload = payload.dict(exclude_none=True) if payload is not None else None
    hb = HeartbeatModel(check_id=check.id, payload=json.dumps(hb_payload) if hb_payload is not None else None)
    session.add(hb)

    # update check status atomically-ish
    prev_status = check.status
    check.last_ping = datetime.utcnow()
    check.consecutive_failures = 0
    check.status = "UP"
    session.add(check)

    # if previously DOWN, emit recovery event for history and alerts
    if prev_status != "UP":
        event = EventModel(check_id=check.id, project_id=project_id, event_type=EventType.UP, message="Recovered via heartbeat")
        session.add(event)

    session.commit()
    session.refresh(check)
    return {"status": check.status, "last_ping": check.last_ping}

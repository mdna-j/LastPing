import os
from datetime import datetime, timedelta
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
    hb_payload_json = payload.json(exclude_none=True) if payload is not None else None
    incoming_ts = payload.timestamp if payload and payload.timestamp else datetime.utcnow()
    hb = HeartbeatModel(
        check_id=check.id,
        timestamp=incoming_ts,
        payload=hb_payload_json,
    )
    session.add(hb)

    # Ignore stale heartbeats that are older than the current last_ping by a tolerance window.
    stale_tolerance = max(0, int(os.environ.get("HEARTBEAT_STALE_TOLERANCE_SECONDS", "30")))
    current_last_ping = getattr(check, "last_ping", None)
    if current_last_ping and incoming_ts < (current_last_ping - timedelta(seconds=stale_tolerance)):
        session.commit()
        session.refresh(check)
        return {"status": check.status, "last_ping": check.last_ping, "stale_ignored": True}

    # update check status
    prev_status = check.status
    check.last_ping = incoming_ts
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

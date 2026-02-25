import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Literal

from fastapi import APIRouter, Depends, Body, HTTPException, Header, Path
from pydantic import constr, root_validator
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, Event as EventModel, EventType, CheckType, Project
from ..deps import require_project_api_key, limit_by_api_key
from ..schemas import StrictBaseModel
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["webhooks"]) 


class WebhookIn(StrictBaseModel):
    check_name: constr(min_length=1, max_length=120)
    event: Optional[Literal["down", "up", "heartbeat", "http_failure", "degraded"]] = "down"
    message: Optional[constr(max_length=1000)] = None
    timestamp: Optional[datetime] = None

    @root_validator(pre=True)
    def _coerce_legacy_fields(cls, values):
        # Support legacy keys while still rejecting unexpected fields.
        if "check_name" not in values:
            for alt in ("check", "name"):
                if alt in values:
                    values["check_name"] = values.pop(alt)
                    break
        if "event" not in values and "type" in values:
            values["event"] = values.pop("type")
        if "event" in values and isinstance(values["event"], str):
            values["event"] = values["event"].lower()
        if "message" not in values and "body" in values:
            values["message"] = values.pop("body")
        return values


def _try_limit(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Call `limit_by_api_key` but return None on 401/403 so primary project API key (verified by `require_project_api_key`) still works.

    Returns the matched ApiKey or `None` when rate-limiting is not applicable.
    """
    try:
        return limit_by_api_key(project_id=project_id, authorization=authorization, x_api_key=x_api_key, x_admin_token=x_admin_token, session=session)
    except HTTPException as he:
        if he.status_code in (401, 403):
            return None
        raise


@router.post("/webhook")
def receive_webhook(project_id: int = Path(..., ge=1), payload: Optional[WebhookIn] = Body(None), _proj: Project = Depends(require_project_api_key), _rl = Depends(_try_limit), session: Session = Depends(get_session)):
    """Generic inbound webhook endpoint.

    Expected JSON payload (flexible):
    {
      "check_name": "name",
      "event": "down" | "up" | "heartbeat",
      "message": "optional message",
      "timestamp": "ISO8601 optional"
    }

    This will auto-create a `Check` if not found, record an `Event`, and
    update `last_ping` for heartbeat events. It respects project/check
    maintenance windows and returns 202 on acceptance.
    """
    # normalize payload
    if payload is None:
        raise HTTPException(status_code=400, detail="Payload required")
    name = payload.check_name
    ev_type = (payload.event or "down").lower()
    msg = payload.message
    ts = payload.timestamp

    logger.info("webhook received project_id=%s check=%s event=%s", project_id, name, ev_type)

    chk = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == name)).first()
    if not chk:
        chk = CheckModel(project_id=project_id, name=name, type=CheckType.HTTP)
        session.add(chk)
        session.commit()
        session.refresh(chk)
        logger.info("created check id=%s name=%s", chk.id, chk.name)

    proj = session.get(Project, project_id)

    # respect maintenance windows
    now = datetime.utcnow()
    from ..worker import _in_maintenance
    if _in_maintenance(chk, proj, now):
        # create suppressed event for history
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DOWN if ev_type==EventType.DOWN else ev_type, message=(msg or "suppressed due to maintenance"))
        session.add(e)
        session.commit()
        logger.info("suppressed event created for check_id=%s", chk.id)
        return {"accepted": True, "suppressed": True}

    # map heartbeat to update last_ping
    if ev_type == EventType.HEARTBEAT:
        incoming_ts = ts or datetime.utcnow()
        stale_tolerance = max(0, int(os.environ.get("HEARTBEAT_STALE_TOLERANCE_SECONDS", "30")))
        is_stale = bool(
            chk.last_ping
            and incoming_ts < (chk.last_ping - timedelta(seconds=stale_tolerance))
        )
        if not is_stale:
            chk.last_ping = incoming_ts
            chk.consecutive_failures = 0
            chk.status = "UP"
            session.add(chk)
        # add heartbeat event
        e = EventModel(
            check_id=chk.id,
            project_id=project_id,
            event_type=EventType.HEARTBEAT,
            message=(msg or ("stale heartbeat ignored" if is_stale else None)),
        )
        session.add(e)
        session.commit()
        logger.info("heartbeat recorded for check_id=%s last_ping=%s stale=%s", chk.id, chk.last_ping, is_stale)
        return {"accepted": True, "status": chk.status, "stale_ignored": is_stale}

    # create event for UP/DOWN/HTTP failure
    if ev_type == EventType.DOWN:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DOWN, message=msg)
    elif ev_type == EventType.UP:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.UP, message=msg)
    elif ev_type == EventType.HTTP_FAILURE:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.HTTP_FAILURE, message=msg)
    elif ev_type == EventType.DEGRADED:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DEGRADED, message=msg)
    else:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DOWN, message=msg)

    session.add(e)
    # update check status
    if ev_type in (EventType.DOWN, EventType.HTTP_FAILURE):
        chk.status = "DOWN"
        chk.consecutive_failures = (chk.consecutive_failures or 0) + 1
    elif ev_type == EventType.DEGRADED:
        chk.status = "DEGRADED"
        chk.consecutive_failures = (chk.consecutive_failures or 0) + 1
    elif ev_type == EventType.UP:
        chk.status = "UP"
        chk.consecutive_failures = 0
    session.add(chk)
    session.commit()
    logger.info("event committed check_id=%s event_id=%s", chk.id, getattr(e, 'id', None))
    return {"accepted": True}

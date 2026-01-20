from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Body, HTTPException, Header
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, Event as EventModel, EventType, CheckType, Project
from ..deps import require_project_api_key, limit_by_api_key
import logging
from .. import db as _db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["webhooks"]) 


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
def receive_webhook(project_id: int, payload: Optional[Dict[str, Any]] = Body(None), _proj: Project = Depends(require_project_api_key), _rl = Depends(_try_limit), session: Session = Depends(get_session)):
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
    # quick instrumentation
    try:
        logger.info("webhook called for project_id=%s payload=%s", project_id, payload)
        logger.info("module DB URL: %s", getattr(_db.engine, 'url', None))
    except Exception:
        logger.exception("unable to log DB url")

    # normalize payload
    if payload is None:
        payload = {}
    name = payload.get("check_name") or payload.get("check") or payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing check_name in payload")

    ev_type = (payload.get("event") or payload.get("type") or "down").lower()
    if ev_type not in (EventType.DOWN, EventType.UP, EventType.HEARTBEAT):
        ev_type = EventType.DOWN

    msg = payload.get("message") or payload.get("body") or None
    ts = None
    if payload.get("timestamp"):
        try:
            ts = datetime.fromisoformat(payload.get("timestamp"))
        except Exception:
            ts = None

    # find or create check
    try:
        bind = session.get_bind()
        logger.info("session bind: %s", getattr(bind, 'engine', getattr(bind, 'url', bind)))
    except Exception:
        logger.exception("unable to get session bind")

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
        chk.last_ping = ts or datetime.utcnow()
        chk.consecutive_failures = 0
        chk.status = "UP"
        session.add(chk)
        # add heartbeat event
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.HEARTBEAT, message=msg)
        session.add(e)
        session.commit()
        logger.info("heartbeat recorded for check_id=%s last_ping=%s", chk.id, chk.last_ping)
        return {"accepted": True, "status": chk.status}

    # create event for UP/DOWN/HTTP failure
    if ev_type == EventType.DOWN:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DOWN, message=msg)
    elif ev_type == EventType.UP:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.UP, message=msg)
    else:
        e = EventModel(check_id=chk.id, project_id=project_id, event_type=EventType.DOWN, message=msg)

    session.add(e)
    # update check status
    if ev_type == EventType.DOWN:
        chk.status = "DOWN"
        chk.consecutive_failures = (chk.consecutive_failures or 0) + 1
    elif ev_type == EventType.UP:
        chk.status = "UP"
        chk.consecutive_failures = 0
    session.add(chk)
    session.commit()
    logger.info("event committed check_id=%s event_id=%s", chk.id, getattr(e, 'id', None))
    return {"accepted": True}

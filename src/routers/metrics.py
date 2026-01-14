from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project
from ..deps import require_project_api_key

router = APIRouter(prefix="/projects/{project_id}", tags=["metrics"])


def _parse_range(start: Optional[str], end: Optional[str]) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    if start:
        start_dt = datetime.fromisoformat(start)
    else:
        start_dt = now - (now - datetime.utcfromtimestamp(0))  # epoch fallback; will be corrected below
    if end:
        end_dt = datetime.fromisoformat(end)
    else:
        end_dt = now
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")
    return start_dt, end_dt


@router.get("/metrics/uptime")
def uptime(project_id: int, check_id: Optional[int] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return uptime percentage for a check or project over the given ISO8601 time range.

    If `check_id` is omitted, returns aggregated uptime across all checks (time-weighted).
    """
    start_dt = datetime.fromisoformat(start) if start else datetime.utcnow() - timedelta(days=7)
    end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    def compute_for_check(cid: int) -> float:
        stmt = select(Event).where(Event.project_id == project_id, Event.check_id == cid).order_by(Event.created_at)
        events: List[Event] = session.exec(stmt).all()
        # Basic state tracking
        # Determine state at window start
        prev_stmt = select(Event).where(Event.project_id == project_id, Event.check_id == cid, Event.created_at < start_dt).order_by(Event.created_at.desc())
        prev = session.exec(prev_stmt).first()
        current_state = "up"
        if prev and prev.event_type in ("down", "http_failure"):
            current_state = "down"
        downtime = 0.0
        last_change = start_dt
        for ev in events:
            if ev.created_at < start_dt:
                continue
            if ev.created_at > end_dt:
                break
            if ev.event_type in ("down", "http_failure") and current_state == "up":
                # down starts
                current_state = "down"
                last_change = ev.created_at
            elif ev.event_type == "up" and current_state == "down":
                # down ends
                downtime += (ev.created_at - last_change).total_seconds()
                current_state = "up"
                last_change = ev.created_at
        # if still down at end of window
        if current_state == "down":
            downtime += (end_dt - last_change).total_seconds()
        total = (end_dt - start_dt).total_seconds()
        uptime_pct = max(0.0, (total - downtime) / total * 100.0)
        return uptime_pct

    if check_id:
        pct = compute_for_check(check_id)
        return {"project_id": project_id, "check_id": check_id, "uptime": pct}

    # aggregate across checks (simple average of check uptimes)
    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
    if not checks:
        raise HTTPException(status_code=404, detail="No checks found for project")
    vals = [compute_for_check(c.id) for c in checks]
    agg = sum(vals) / len(vals)
    return {"project_id": project_id, "uptime": agg}


@router.get("/events/timeline")
def timeline(project_id: int, check_id: Optional[int] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None), limit: int = Query(100), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    start_dt = datetime.fromisoformat(start) if start else datetime.utcnow() - timedelta(days=7)
    end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")
    stmt = select(Event).where(Event.project_id == project_id, Event.created_at >= start_dt, Event.created_at <= end_dt)
    if check_id:
        stmt = stmt.where(Event.check_id == check_id)
    stmt = stmt.order_by(Event.created_at.desc()).limit(limit)
    evs = session.exec(stmt).all()
    out = []
    for e in evs:
        out.append({"id": e.id, "check_id": e.check_id, "type": e.event_type, "message": e.message, "ts": e.created_at.isoformat()})
    return out


@router.get("/metrics/mttr")
def mttr(project_id: int, check_id: Optional[int] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    start_dt = datetime.fromisoformat(start) if start else datetime.utcnow() - timedelta(days=30)
    end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    def compute_mttr_for_check(cid: int) -> Optional[float]:
        stmt = select(Event).where(Event.project_id == project_id, Event.check_id == cid, Event.created_at >= start_dt, Event.created_at <= end_dt).order_by(Event.created_at)
        events: List[Event] = session.exec(stmt).all()
        downs = []
        for i, ev in enumerate(events):
            if ev.event_type in ("down", "http_failure"):
                # find next up
                for j in range(i+1, len(events)):
                    if events[j].event_type == "up":
                        dur = (events[j].created_at - ev.created_at).total_seconds()
                        downs.append(dur)
                        break
        if not downs:
            return None
        return sum(downs) / len(downs)

    if check_id:
        val = compute_mttr_for_check(check_id)
        return {"project_id": project_id, "check_id": check_id, "mttr_seconds": val}

    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
    vals = [compute_mttr_for_check(c.id) for c in checks]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"project_id": project_id, "mttr_seconds": None}
    agg = sum(vals) / len(vals)
    return {"project_id": project_id, "mttr_seconds": agg}

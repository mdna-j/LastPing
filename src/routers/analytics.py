from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project
from ..deps import require_project_api_key

router = APIRouter(prefix="/projects/{project_id}", tags=["analytics"])


@router.get("/analytics/failures")
def failure_summary(project_id: int, days: int = Query(30), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return top failing checks by down events in the window."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    stmt = select(Event).where(Event.project_id == project_id, Event.created_at >= start_dt, Event.created_at <= end_dt)
    events = session.exec(stmt).all()
    counts: Dict[int, int] = {}
    for ev in events:
        if ev.event_type in ("down", "http_failure"):
            counts[ev.check_id] = counts.get(ev.check_id, 0) + 1
    rows = []
    for check_id, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        chk = session.get(Check, check_id)
        rows.append({"check_id": check_id, "name": getattr(chk, "name", None), "down_events": cnt})
    return {"project_id": project_id, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "top_failures": rows}


@router.get("/analytics/trends")
def failure_trends(project_id: int, days: int = Query(30), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return daily counts of down events for simple trend analysis."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    stmt = select(Event).where(Event.project_id == project_id, Event.created_at >= start_dt, Event.created_at <= end_dt)
    events = session.exec(stmt).all()
    buckets: Dict[str, int] = {}
    for ev in events:
        if ev.event_type in ("down", "http_failure"):
            day = ev.created_at.strftime("%Y-%m-%d")
            buckets[day] = buckets.get(day, 0) + 1
    series = [{"day": k, "down_events": buckets[k]} for k in sorted(buckets.keys())]
    return {"project_id": project_id, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "series": series}

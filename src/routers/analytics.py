from datetime import datetime, timedelta
from typing import Optional, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project, Incident
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
def failure_trends(project_id: int, days: int = Query(30), interval: str = Query("day"), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return aggregated counts of down events for simple trend analysis."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    stmt = select(Event).where(Event.project_id == project_id, Event.created_at >= start_dt, Event.created_at <= end_dt)
    events = session.exec(stmt).all()
    buckets: Dict[str, int] = {}
    interval = interval.lower()
    if interval not in ("hour", "day", "week"):
        raise HTTPException(status_code=400, detail="interval must be hour, day, or week")

    def bucket_key(dt: datetime) -> str:
        if interval == "hour":
            b = dt.replace(minute=0, second=0, microsecond=0)
        elif interval == "week":
            monday = dt - timedelta(days=dt.weekday())
            b = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            b = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return b.isoformat()
    for ev in events:
        if ev.event_type in ("down", "http_failure"):
            key = bucket_key(ev.created_at)
            buckets[key] = buckets.get(key, 0) + 1
    series = [{"bucket_start": k, "down_events": buckets[k]} for k in sorted(buckets.keys())]
    return {"project_id": project_id, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "interval": interval, "series": series}


@router.get("/analytics/similar-incidents")
def similar_incidents(project_id: int, days: int = Query(90), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Group incidents by check and message signature for similarity heuristics."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    incs = session.exec(select(Incident).where(Incident.project_id == project_id, Incident.started_at >= start_dt)).all()

    def normalize(msg: Optional[str]) -> str:
        if not msg:
            return "unknown"
        low = msg.strip().lower()
        for sep in ("(", ":", ";", "|"):
            if sep in low:
                low = low.split(sep, 1)[0].strip()
        return low[:120] if low else "unknown"

    groups: Dict[str, Dict[str, object]] = {}
    for inc in incs:
        ev = session.exec(select(Event).where(Event.incident_id == inc.id).order_by(Event.created_at)).first()
        signature = normalize(getattr(ev, "message", None))
        key = f"{inc.check_id}:{signature}"
        g = groups.get(key)
        if not g:
            groups[key] = {
                "check_id": inc.check_id,
                "signature": signature,
                "incident_ids": [inc.id],
                "count": 1,
                "last_seen": inc.started_at,
            }
        else:
            g["incident_ids"].append(inc.id)
            g["count"] = int(g["count"]) + 1
            if inc.started_at > g["last_seen"]:
                g["last_seen"] = inc.started_at

    rows: List[dict] = []
    for g in groups.values():
        rows.append({
            "check_id": g["check_id"],
            "signature": g["signature"],
            "count": g["count"],
            "incident_ids": g["incident_ids"],
            "last_seen": g["last_seen"].isoformat(),
        })

    rows.sort(key=lambda r: r["count"], reverse=True)
    return {"project_id": project_id, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "groups": rows}

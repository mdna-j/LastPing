from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project
from ..deps import require_project_api_key
from ..models import UptimeSnapshot

router = APIRouter(prefix="/projects/{project_id}", tags=["metrics"])


def _parse_dt(label: str, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{label} must be ISO8601")


def _parse_range(start: Optional[str], end: Optional[str]) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    start_dt = _parse_dt("start", start) or now - (now - datetime.utcfromtimestamp(0))
    end_dt = _parse_dt("end", end) or now
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")
    return start_dt, end_dt


@router.get("/metrics/uptime")
def uptime(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return uptime percentage for a check or project over the given ISO8601 time range.

    If `check_id` is omitted, returns aggregated uptime across all checks (time-weighted).
    """
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=7)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
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
def timeline(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=7)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
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
def mttr(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=30)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
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


@router.get("/metrics/availability")
def availability_report(project_id: int = Path(..., ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return availability report with SLO/SLA compliance for a project."""
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=30)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    def compute_for_check(cid: int) -> float:
        stmt = select(Event).where(Event.project_id == project_id, Event.check_id == cid).order_by(Event.created_at)
        events: List[Event] = session.exec(stmt).all()
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
                current_state = "down"
                last_change = ev.created_at
            elif ev.event_type == "up" and current_state == "down":
                downtime += (ev.created_at - last_change).total_seconds()
                current_state = "up"
                last_change = ev.created_at
        if current_state == "down":
            downtime += (end_dt - last_change).total_seconds()
        total = (end_dt - start_dt).total_seconds()
        return max(0.0, (total - downtime) / total * 100.0) if total > 0 else 100.0

    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
    check_rows = []
    for c in checks:
        pct = compute_for_check(c.id)
        check_rows.append({
            "check_id": c.id,
            "name": c.name,
            "uptime_percent": pct,
            "slo_met": (pct >= (project.slo_target or 0)),
            "sla_met": (pct >= (project.sla_target or 0)),
        })

    agg = sum([r["uptime_percent"] for r in check_rows]) / len(check_rows) if check_rows else 100.0
    return {
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "project_uptime_percent": agg,
        "slo_target": project.slo_target,
        "sla_target": project.sla_target,
        "checks": check_rows,
    }


@router.get("/metrics/snapshots")
def snapshots(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return recent UptimeSnapshot rows for a project (optionally filtered by check_id)."""
    stmt = select(UptimeSnapshot).where(UptimeSnapshot.project_id == project_id)
    if check_id:
        stmt = stmt.where(UptimeSnapshot.check_id == check_id)
    stmt = stmt.order_by(UptimeSnapshot.window_end.desc()).limit(limit)
    rows = session.exec(stmt).all()
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "project_id": r.project_id,
            "check_id": r.check_id,
            "window_start": r.window_start.isoformat(),
            "window_end": r.window_end.isoformat(),
            "uptime_percent": r.uptime_percent,
            "mttr_seconds": r.mttr_seconds,
        })
    return out

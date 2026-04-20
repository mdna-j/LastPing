from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project, AvailabilityRollup, SLOCompliancePeriod
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


def _rollup_key(dt: datetime, period: str) -> str:
    if period == "month":
        return dt.strftime("%Y-%m")
    if period == "quarter":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    return dt.date().isoformat()


def _rollup_series(series: List[dict], period: str, slo_target: Optional[float], sla_target: Optional[float]) -> List[dict]:
    buckets: dict = {}
    for row in series:
        day = row.get("day")
        if not day:
            continue
        dt = datetime.fromisoformat(day)
        key = _rollup_key(dt, period)
        buckets.setdefault(key, []).append(row.get("uptime_percent", 0.0))

    out = []
    for key in sorted(buckets.keys()):
        vals = buckets[key]
        avg = sum(vals) / len(vals) if vals else 0.0
        out.append({
            "period": key,
            "uptime_percent": avg,
            "slo_met": (slo_target is not None and avg >= slo_target) if slo_target is not None else None,
            "sla_met": (sla_target is not None and avg >= sla_target) if sla_target is not None else None,
        })
    return out


def _load_slo_compliance_series(
    session: Session,
    *,
    project_id: int,
    period_type: str,
    check_id: Optional[int],
    start_dt: datetime,
    end_dt: datetime,
) -> List[dict]:
    stmt = (
        select(SLOCompliancePeriod)
        .where(
            SLOCompliancePeriod.project_id == project_id,
            SLOCompliancePeriod.period_type == period_type,
            SLOCompliancePeriod.period_end > start_dt,
            SLOCompliancePeriod.period_start < end_dt,
        )
        .order_by(SLOCompliancePeriod.period_start)
    )
    if check_id is None:
        stmt = stmt.where(SLOCompliancePeriod.check_id == None)
    else:
        stmt = stmt.where(SLOCompliancePeriod.check_id == check_id)

    rows = session.exec(stmt).all()
    key_name = "day" if period_type == "day" else "period"
    return [
        {
            key_name: row.period,
            "uptime_percent": row.uptime_percent,
            "slo_met": row.slo_met,
            "sla_met": row.sla_met,
            "source": "stored",
        }
        for row in rows
    ]


def _merge_series_with_preference(primary_series: List[dict], fallback_series: List[dict], key_name: str) -> List[dict]:
    merged = {}
    for row in fallback_series:
        key = row.get(key_name)
        if key:
            merged[key] = row
    for row in primary_series:
        key = row.get(key_name)
        if key:
            merged[key] = row
    return [merged[key] for key in sorted(merged.keys())]


def _historical_backing_store(stored_series: List[dict], fallback_series: List[dict], fallback_name: str) -> str:
    if stored_series and fallback_series:
        return "mixed"
    if stored_series:
        return "slo_compliance_period"
    return fallback_name


def _compute_check_uptime_percent(session: Session, project_id: int, check_id: int, start_dt: datetime, end_dt: datetime) -> float:
    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.check_id == check_id,
    ).order_by(Event.created_at)
    events: List[Event] = session.exec(stmt).all()
    prev_stmt = select(Event).where(
        Event.project_id == project_id,
        Event.check_id == check_id,
        Event.created_at < start_dt,
    ).order_by(Event.created_at.desc())
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


def _load_project_checks(session: Session, project_id: int) -> List[Check]:
    return session.exec(select(Check).where(Check.project_id == project_id)).all()


def _compute_project_uptime_percent(
    session: Session,
    project_id: int,
    start_dt: datetime,
    end_dt: datetime,
    checks: Optional[List[Check]] = None,
) -> float:
    checks = checks if checks is not None else _load_project_checks(session, project_id)
    if not checks:
        return 100.0
    vals = [_compute_check_uptime_percent(session, project_id, check.id, start_dt, end_dt) for check in checks]
    return sum(vals) / len(vals)


def _compute_project_check_rows(session: Session, project: Project, start_dt: datetime, end_dt: datetime) -> tuple[List[Check], List[dict], float]:
    checks = _load_project_checks(session, project.id)
    check_rows = []
    for check in checks:
        pct = _compute_check_uptime_percent(session, project.id, check.id, start_dt, end_dt)
        check_rows.append({
            "check_id": check.id,
            "name": check.name,
            "uptime_percent": pct,
            "slo_met": (pct >= (project.slo_target or 0)),
            "sla_met": (pct >= (project.sla_target or 0)),
        })
    agg = sum([row["uptime_percent"] for row in check_rows]) / len(check_rows) if check_rows else 100.0
    return checks, check_rows, agg


def _compute_budget_breakdown(uptime_percent: float, slo_target: Optional[float], total_seconds: float) -> dict:
    target = float(slo_target or 100.0)
    budget_percent = max(0.0, 100.0 - target)
    error_rate_percent = max(0.0, 100.0 - float(uptime_percent or 0.0))
    consumed_seconds = max(0.0, total_seconds) * error_rate_percent / 100.0
    budget_seconds = max(0.0, total_seconds) * budget_percent / 100.0
    consumed_percent = (consumed_seconds / budget_seconds * 100.0) if budget_seconds > 0 else None
    remaining_seconds = max(0.0, budget_seconds - consumed_seconds)
    remaining_percent = max(0.0, 100.0 - consumed_percent) if consumed_percent is not None else None
    return {
        "slo_target": target,
        "error_budget_percent": budget_percent,
        "uptime_percent": uptime_percent,
        "error_rate_percent": error_rate_percent,
        "budget_seconds": budget_seconds,
        "consumed_seconds": consumed_seconds,
        "remaining_seconds": remaining_seconds,
        "consumed_percent": consumed_percent,
        "remaining_percent": remaining_percent,
    }


def _compute_burn_rate_windows(
    session: Session,
    project_id: int,
    *,
    start_dt: datetime,
    end_dt: datetime,
    checks: List[Check],
    error_budget_percent: float,
    window_specs: List[tuple[str, int, float]],
) -> List[dict]:
    windows = []
    for label, minutes, threshold in window_specs:
        window_start = max(start_dt, end_dt - timedelta(minutes=max(1, minutes)))
        uptime_percent = _compute_project_uptime_percent(session, project_id, window_start, end_dt, checks=checks)
        error_rate_percent = max(0.0, 100.0 - uptime_percent)
        burn_rate = (error_rate_percent / error_budget_percent) if error_budget_percent > 0 else None
        windows.append(
            {
                "label": label,
                "minutes": max(1, minutes),
                "uptime_percent": uptime_percent,
                "error_rate_percent": error_rate_percent,
                "burn_rate": burn_rate,
                "threshold": threshold,
            }
        )
    return windows


def _compliance_summary(series: List[dict]) -> dict:
    met = 0
    missed = 0
    unknown = 0
    for row in series:
        value = row.get("slo_met")
        if value is True:
            met += 1
        elif value is False:
            missed += 1
        else:
            unknown += 1
    return {
        "total": len(series),
        "met": met,
        "missed": missed,
        "unknown": unknown,
    }


def _compute_error_budget_status(
    session: Session,
    project_id: int,
    start_dt: datetime,
    end_dt: datetime,
    *,
    short_window_minutes: int = 60,
    long_window_minutes: int = 360,
    short_burn_threshold: float = 14.4,
    long_burn_threshold: float = 6.0,
) -> dict:
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    checks, check_rows, agg = _compute_project_check_rows(session, project, start_dt, end_dt)
    total_seconds = max(0.0, (end_dt - start_dt).total_seconds())
    budget = _compute_budget_breakdown(agg, project.slo_target, total_seconds)
    budget_percent = budget["error_budget_percent"]
    burn_windows = _compute_burn_rate_windows(
        session,
        project_id,
        start_dt=start_dt,
        end_dt=end_dt,
        checks=checks,
        error_budget_percent=budget_percent,
        window_specs=[
            (f"{max(1, short_window_minutes)}m", short_window_minutes, short_burn_threshold),
            (f"{max(1, long_window_minutes)}m", long_window_minutes, long_burn_threshold),
        ],
    )
    short_window = burn_windows[0]
    long_window = burn_windows[1]
    short_burn = short_window["burn_rate"]
    long_burn = long_window["burn_rate"]

    alert_triggered = (
        budget_percent > 0
        and short_burn is not None
        and long_burn is not None
        and short_burn >= short_burn_threshold
        and long_burn >= long_burn_threshold
    )
    burn_reason = None
    if alert_triggered:
        burn_reason = (
            f"burn-rate alert: {project.name} is consuming error budget at "
            f"{short_burn:.2f}x over {max(1, short_window_minutes)}m and "
            f"{long_burn:.2f}x over {max(1, long_window_minutes)}m"
        )

    top_offenders = sorted(check_rows, key=lambda row: row["uptime_percent"])[:3]
    return {
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "slo_target": project.slo_target,
        "error_budget_percent": budget["error_budget_percent"],
        "project_uptime_percent": agg,
        "budget_seconds": budget["budget_seconds"],
        "consumed_seconds": budget["consumed_seconds"],
        "remaining_seconds": budget["remaining_seconds"],
        "consumed_percent": budget["consumed_percent"],
        "remaining_percent": budget["remaining_percent"],
        "top_offenders": top_offenders,
        "burn_rate_windows": burn_windows,
        "alert": {
            "triggered": alert_triggered,
            "reason": burn_reason,
            "short_threshold": short_burn_threshold,
            "long_threshold": long_burn_threshold,
        },
    }


def _compute_availability_report(session: Session, project_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _checks, check_rows, agg = _compute_project_check_rows(session, project, start_dt, end_dt)
    project_slo_met = None if project.slo_target is None else agg >= project.slo_target
    project_sla_met = None if project.sla_target is None else agg >= project.sla_target
    return {
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "project_uptime_percent": agg,
        "slo_target": project.slo_target,
        "sla_target": project.sla_target,
        "project_slo_met": project_slo_met,
        "project_sla_met": project_sla_met,
        "checks": check_rows,
    }


def _compute_slo_dashboard(
    session: Session,
    project_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> dict:
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    checks, check_rows, agg = _compute_project_check_rows(session, project, start_dt, end_dt)
    total_seconds = max(0.0, (end_dt - start_dt).total_seconds())
    project_budget = _compute_budget_breakdown(agg, project.slo_target, total_seconds)
    burn_windows = _compute_burn_rate_windows(
        session,
        project_id,
        start_dt=start_dt,
        end_dt=end_dt,
        checks=checks,
        error_budget_percent=project_budget["error_budget_percent"],
        window_specs=[
            ("1h", 60, 14.4),
            ("6h", 360, 6.0),
            ("24h", 1440, 3.0),
        ],
    )

    component_rows = []
    total_component_consumed_seconds = 0.0
    for row in check_rows:
        budget = _compute_budget_breakdown(row["uptime_percent"], project.slo_target, total_seconds)
        total_component_consumed_seconds += float(budget["consumed_seconds"] or 0.0)
        component_rows.append(
            {
                **row,
                "error_rate_percent": budget["error_rate_percent"],
                "budget_seconds": budget["budget_seconds"],
                "consumed_seconds": budget["consumed_seconds"],
                "remaining_seconds": budget["remaining_seconds"],
                "consumed_percent": budget["consumed_percent"],
                "remaining_percent": budget["remaining_percent"],
            }
        )

    for row in component_rows:
        consumed_seconds = float(row.get("consumed_seconds") or 0.0)
        row["consumed_share_percent"] = (
            consumed_seconds / total_component_consumed_seconds * 100.0
            if total_component_consumed_seconds > 0
            else 0.0
        )

    component_rows.sort(
        key=lambda row: (
            -(float(row.get("consumed_percent") or 0.0)),
            float(row.get("uptime_percent") or 0.0),
            str(row.get("name") or ""),
        )
    )

    daily_history = availability_history(
        project_id=project_id,
        check_id=None,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        session=session,
        _proj=project,
    )
    monthly_history = availability_rollup(
        project_id=project_id,
        period="month",
        check_id=None,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        session=session,
        _proj=project,
    )
    stored_daily_series = _load_slo_compliance_series(
        session,
        project_id=project_id,
        period_type="day",
        check_id=None,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    stored_monthly_series = _load_slo_compliance_series(
        session,
        project_id=project_id,
        period_type="month",
        check_id=None,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    daily_series = _merge_series_with_preference(stored_daily_series, daily_history.get("series", []), "day")
    monthly_series = _merge_series_with_preference(stored_monthly_series, monthly_history.get("series", []), "period")

    top_offenders = component_rows[:5]
    critical_burn = any(
        window.get("burn_rate") is not None and float(window["burn_rate"]) >= float(window.get("threshold") or 0.0)
        for window in burn_windows
    )
    warning_burn = any(
        window.get("burn_rate") is not None and float(window["burn_rate"]) >= 1.0
        for window in burn_windows
    )
    remaining_pct = project_budget["remaining_percent"]
    if critical_burn:
        overall_state = "critical"
    elif warning_burn or (remaining_pct is not None and remaining_pct <= 25):
        overall_state = "warning"
    else:
        overall_state = "healthy"

    return {
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "summary": {
            "state": overall_state,
            "slo_target": project.slo_target,
            "project_uptime_percent": agg,
            "error_budget_percent": project_budget["error_budget_percent"],
            "budget_seconds": project_budget["budget_seconds"],
            "consumed_seconds": project_budget["consumed_seconds"],
            "remaining_seconds": project_budget["remaining_seconds"],
            "consumed_percent": project_budget["consumed_percent"],
            "remaining_percent": project_budget["remaining_percent"],
        },
        "burn_rate_windows": burn_windows,
        "service_budget_split": {
            "component_count": len(component_rows),
            "total_consumed_seconds": total_component_consumed_seconds,
            "checks": component_rows,
        },
        "top_offenders": top_offenders,
        "historical_compliance": {
            "daily": {
                "series": daily_series,
                "summary": _compliance_summary(daily_series),
                "backing_store": _historical_backing_store(
                    stored_daily_series,
                    daily_history.get("series", []),
                    "uptime_snapshot",
                ),
            },
            "monthly": {
                "series": monthly_series,
                "summary": _compliance_summary(monthly_series),
                "backing_store": _historical_backing_store(
                    stored_monthly_series,
                    monthly_history.get("series", []),
                    "availability_rollup",
                ),
            },
        },
    }


@router.get("/metrics/uptime")
def uptime(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return uptime percentage for a check or project over the given ISO8601 time range.

    If `check_id` is omitted, returns aggregated uptime across all checks (time-weighted).
    """
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=7)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")

    if check_id:
        pct = _compute_check_uptime_percent(session, project_id, check_id, start_dt, end_dt)
        return {"project_id": project_id, "check_id": check_id, "uptime": pct}

    # aggregate across checks (simple average of check uptimes)
    checks = _load_project_checks(session, project_id)
    if not checks:
        raise HTTPException(status_code=404, detail="No checks found for project")
    agg = _compute_project_uptime_percent(session, project_id, start_dt, end_dt, checks=checks)
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
    return _compute_availability_report(session, project_id, start_dt, end_dt)


@router.get("/metrics/error-budget")
def error_budget_report(
    project_id: int = Path(..., ge=1),
    start: Optional[str] = Query(None, max_length=40),
    end: Optional[str] = Query(None, max_length=40),
    short_window_minutes: int = Query(60, ge=1, le=1440),
    long_window_minutes: int = Query(360, ge=1, le=10080),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=30)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
    return _compute_error_budget_status(
        session,
        project_id,
        start_dt,
        end_dt,
        short_window_minutes=short_window_minutes,
        long_window_minutes=long_window_minutes,
    )


@router.get("/metrics/slo-dashboard")
def slo_dashboard(
    project_id: int = Path(..., ge=1),
    start: Optional[str] = Query(None, max_length=40),
    end: Optional[str] = Query(None, max_length=40),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    start_dt = _parse_dt("start", start) if start else datetime.utcnow() - timedelta(days=30)
    end_dt = _parse_dt("end", end) if end else datetime.utcnow()
    return _compute_slo_dashboard(session, project_id, start_dt, end_dt)


@router.get("/metrics/availability/history")
def availability_history(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return daily availability history using UptimeSnapshot rows."""
    start_dt, end_dt = _parse_range(start, end)
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    stmt = select(UptimeSnapshot).where(UptimeSnapshot.project_id == project_id, UptimeSnapshot.window_end >= start_dt, UptimeSnapshot.window_end <= end_dt)
    if check_id:
        stmt = stmt.where(UptimeSnapshot.check_id == check_id)
    stmt = stmt.order_by(UptimeSnapshot.window_end.desc())
    snaps = session.exec(stmt).all()

    # keep latest snapshot per day per check
    latest: dict = {}
    for s in snaps:
        day = s.window_end.date().isoformat()
        key = (day, s.check_id)
        if key not in latest:
            latest[key] = s

    # aggregate by day
    days: dict = {}
    for (day, _cid), snap in latest.items():
        days.setdefault(day, []).append(snap)

    series = []
    for day in sorted(days.keys()):
        rows = days[day]
        if check_id:
            snap = rows[0]
            pct = snap.uptime_percent
            series.append({
                "day": day,
                "uptime_percent": pct,
                "slo_met": (project.slo_target is not None and pct >= project.slo_target) if project.slo_target is not None else None,
                "sla_met": (project.sla_target is not None and pct >= project.sla_target) if project.sla_target is not None else None,
            })
        else:
            avg = sum([r.uptime_percent for r in rows]) / len(rows)
            series.append({
                "day": day,
                "uptime_percent": avg,
                "slo_met": (project.slo_target is not None and avg >= project.slo_target) if project.slo_target is not None else None,
                "sla_met": (project.sla_target is not None and avg >= project.sla_target) if project.sla_target is not None else None,
            })

    return {
        "project_id": project_id,
        "check_id": check_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "slo_target": project.slo_target,
        "sla_target": project.sla_target,
        "series": series,
    }


@router.get("/metrics/availability/report.csv")
def availability_report_csv(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    data = availability_history(project_id=project_id, check_id=check_id, start=start, end=end, session=session, _proj=_proj)
    lines = []
    lines.append("day,uptime_percent,slo_met,sla_met")
    for row in data.get("series", []):
        lines.append(f"{row['day']},{row['uptime_percent']},{row.get('slo_met')},{row.get('sla_met')}")
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")


@router.get("/metrics/availability/rollup")
def availability_rollup(project_id: int = Path(..., ge=1), period: str = Query("month", max_length=10), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Return monthly/quarterly availability rollups using daily snapshots."""
    period = period.lower()
    if period not in ("month", "quarter"):
        raise HTTPException(status_code=400, detail="period must be month or quarter")
    start_dt, end_dt = _parse_range(start, end)
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Prefer precomputed monthly/quarterly rollups when available.
    if period in ("month", "quarter"):
        stmt = select(AvailabilityRollup).where(
            AvailabilityRollup.project_id == project_id,
            AvailabilityRollup.period_type == period,
            AvailabilityRollup.period_start >= start_dt,
            AvailabilityRollup.period_end <= end_dt,
        )
        if check_id:
            stmt = stmt.where(AvailabilityRollup.check_id == check_id)
        else:
            stmt = stmt.where(AvailabilityRollup.check_id == None)
        rows = session.exec(stmt.order_by(AvailabilityRollup.period_start)).all()
        if rows:
            series = [
                {
                    "period": r.period,
                    "uptime_percent": r.uptime_percent,
                    "slo_met": r.slo_met,
                    "sla_met": r.sla_met,
                }
                for r in rows
            ]
            return {
                "project_id": project_id,
                "check_id": check_id,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "period": period,
                "slo_target": project.slo_target,
                "sla_target": project.sla_target,
                "series": series,
            }

    data = availability_history(project_id=project_id, check_id=check_id, start=start, end=end, session=session, _proj=_proj)
    series = _rollup_series(data.get("series", []), period, data.get("slo_target"), data.get("sla_target"))
    return {
        "project_id": project_id,
        "check_id": check_id,
        "start": data.get("start"),
        "end": data.get("end"),
        "period": period,
        "slo_target": data.get("slo_target"),
        "sla_target": data.get("sla_target"),
        "series": series,
    }


@router.get("/metrics/availability/rollup.csv")
def availability_rollup_csv(project_id: int = Path(..., ge=1), period: str = Query("month", max_length=10), check_id: Optional[int] = Query(None, ge=1), start: Optional[str] = Query(None, max_length=40), end: Optional[str] = Query(None, max_length=40), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    data = availability_rollup(project_id=project_id, period=period, check_id=check_id, start=start, end=end, session=session, _proj=_proj)
    lines = []
    lines.append("period,uptime_percent,slo_met,sla_met")
    for row in data.get("series", []):
        lines.append(f"{row['period']},{row['uptime_percent']},{row.get('slo_met')},{row.get('sla_met')}")
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")


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

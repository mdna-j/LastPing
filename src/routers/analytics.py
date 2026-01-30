from datetime import datetime, timedelta
from typing import Optional, Dict, List
import re
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project, Incident
from ..analytics_ml import find_similar_incidents
from ..deps import require_project_api_key

router = APIRouter(prefix="/projects/{project_id}", tags=["analytics"])


def _normalize_signature(msg: Optional[str]) -> str:
    if not msg:
        return "unknown"
    low = msg.strip().lower()
    low = re.sub(r"https?://\\S+", "url", low)
    low = re.sub(r"\\b\\d{1,3}(?:\\.\\d{1,3}){3}\\b", "ip", low)
    low = re.sub(r"\\d+", "#", low)
    for sep in ("(", ":", ";", "|"):
        if sep in low:
            low = low.split(sep, 1)[0].strip()
    low = re.sub(r"\\s+", " ", low)
    return low[:160] if low else "unknown"


def _bucket_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _event_weight(event_type: str) -> float:
    if event_type in ("down", "http_failure"):
        return 1.0
    if event_type == "degraded":
        return 0.5
    return 0.0


def _linear_forecast(values: List[float]) -> tuple[float, float, float]:
    """Return (slope, intercept, next_value) for a simple linear trend."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1:
        return 0.0, values[0], values[0]
    x_vals = list(range(n))
    x_mean = sum(x_vals) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, values))
    den = sum((x - x_mean) ** 2 for x in x_vals) or 1.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    next_val = slope * n + intercept
    return slope, intercept, next_val


@router.get("/analytics/failures")
def failure_summary(project_id: int = Path(..., ge=1), days: int = Query(30, ge=1, le=365), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
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
def failure_trends(project_id: int = Path(..., ge=1), days: int = Query(30, ge=1, le=365), interval: str = Query("day", max_length=8), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
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


@router.get("/analytics/patterns")
def failure_patterns(project_id: int = Path(..., ge=1), days: int = Query(30, ge=1, le=365), min_count: int = Query(3, ge=1, le=1000), top: int = Query(20, ge=1, le=200), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Detect recurring failure patterns by comparing recent window vs previous window."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    prev_start = start_dt - timedelta(days=days)
    prev_end = start_dt

    stmt = select(Event).where(Event.project_id == project_id, Event.created_at >= prev_start, Event.created_at <= end_dt)
    events = session.exec(stmt).all()
    recent: Dict[str, int] = {}
    previous: Dict[str, int] = {}
    for ev in events:
        if ev.event_type not in ("down", "http_failure"):
            continue
        sig = _normalize_signature(getattr(ev, "message", None))
        key = f"{ev.check_id}:{sig}"
        if ev.created_at >= start_dt:
            recent[key] = recent.get(key, 0) + 1
        else:
            previous[key] = previous.get(key, 0) + 1

    rows: List[dict] = []
    for key, rcount in recent.items():
        if rcount < min_count:
            continue
        pcount = previous.get(key, 0)
        delta = rcount - pcount
        ratio = (rcount / pcount) if pcount else None
        check_id, signature = key.split(":", 1)
        rows.append({
            "check_id": int(check_id),
            "signature": signature,
            "recent_count": rcount,
            "previous_count": pcount,
            "delta": delta,
            "ratio": ratio,
        })

    rows.sort(key=lambda r: (r["delta"], r["recent_count"]), reverse=True)
    return {
        "project_id": project_id,
        "recent_start": start_dt.isoformat(),
        "recent_end": end_dt.isoformat(),
        "previous_start": prev_start.isoformat(),
        "previous_end": prev_end.isoformat(),
        "patterns": rows[:top],
    }


@router.get("/analytics/similar-incidents")
def similar_incidents(project_id: int = Path(..., ge=1), days: int = Query(90, ge=1, le=365), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    """Group incidents by check and message signature for similarity heuristics."""
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    incs = session.exec(select(Incident).where(Incident.project_id == project_id, Incident.started_at >= start_dt)).all()

    def group_root_id(inc: Incident) -> int:
        if getattr(inc, "merged_into", None):
            return inc.merged_into
        return getattr(inc, "group_id", None) or inc.id

    groups: Dict[str, Dict[str, object]] = {}
    for inc in incs:
        ev = session.exec(select(Event).where(Event.incident_id == inc.id).order_by(Event.created_at)).first()
        signature = _normalize_signature(getattr(ev, "message", None))
        root_id = group_root_id(inc)
        key = f"{root_id}:{signature}"
        g = groups.get(key)
        if not g:
            groups[key] = {
                "group_id": root_id,
                "check_ids": {inc.check_id},
                "signature": signature,
                "incident_ids": [inc.id],
                "count": 1,
                "last_seen": inc.started_at,
            }
        else:
            g["incident_ids"].append(inc.id)
            g["count"] = int(g["count"]) + 1
            g["check_ids"].add(inc.check_id)
            if inc.started_at > g["last_seen"]:
                g["last_seen"] = inc.started_at

    rows: List[dict] = []
    for g in groups.values():
        rows.append({
            "group_id": g["group_id"],
            "check_ids": sorted(list(g["check_ids"])),
            "signature": g["signature"],
            "count": g["count"],
            "incident_ids": g["incident_ids"],
            "last_seen": g["last_seen"].isoformat(),
        })

    rows.sort(key=lambda r: r["count"], reverse=True)
    return {"project_id": project_id, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "groups": rows}


@router.get("/analytics/incident-similarity")
def incident_similarity(
    project_id: int = Path(..., ge=1),
    incident_id: int = Query(..., ge=1),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(5, ge=1, le=50),
    threshold: float = Query(0.35, ge=0.0, le=1.0),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Return ML-style similarity scores for incidents based on TF-IDF token overlap."""
    inc = session.get(Incident, incident_id)
    if not inc or inc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    ev = session.exec(select(Event).where(Event.incident_id == incident_id).order_by(Event.created_at)).first()
    text = getattr(ev, "message", None) if ev else None
    matches = find_similar_incidents(
        session=session,
        project_id=project_id,
        target_text=text,
        days=days,
        limit=limit,
        threshold=threshold,
        target_incident_id=incident_id,
    )
    return {
        "project_id": project_id,
        "incident_id": incident_id,
        "days": days,
        "threshold": threshold,
        "matches": matches,
    }


@router.get("/analytics/early-warning")
def early_warning(
    project_id: int = Path(..., ge=1),
    recent_hours: int = Query(3, ge=1, le=72),
    baseline_days: int = Query(14, ge=2, le=180),
    min_events: int = Query(3, ge=1, le=200),
    z_threshold: float = Query(2.5, ge=0.5, le=10.0),
    ratio_threshold: float = Query(2.0, ge=1.1, le=20.0),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Early-warning heuristics for failure spikes and anomalous patterns."""
    now = datetime.utcnow()
    recent_start = now - timedelta(hours=recent_hours)
    baseline_start = now - timedelta(days=baseline_days)
    if baseline_start >= recent_start:
        raise HTTPException(status_code=400, detail="baseline_days must exceed recent window")

    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= baseline_start,
        Event.created_at <= now,
    )
    events = session.exec(stmt).all()
    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
    check_map = {c.id: c for c in checks}

    baseline_counts: Dict[int, Dict[datetime, float]] = {}
    baseline_total: Dict[int, float] = {}
    recent_total: Dict[int, float] = {}
    recent_sig: Dict[int, Dict[str, int]] = {}

    for ev in events:
        if ev.event_type not in ("down", "http_failure", "degraded"):
            continue
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        if ev.created_at >= recent_start:
            recent_total[ev.check_id] = recent_total.get(ev.check_id, 0.0) + weight
            sig = _normalize_signature(getattr(ev, "message", None))
            recent_sig.setdefault(ev.check_id, {})[sig] = recent_sig.get(ev.check_id, {}).get(sig, 0) + 1
        else:
            bucket = _bucket_hour(ev.created_at)
            baseline_counts.setdefault(ev.check_id, {})[bucket] = baseline_counts.get(ev.check_id, {}).get(bucket, 0.0) + weight
            baseline_total[ev.check_id] = baseline_total.get(ev.check_id, 0.0) + weight

    baseline_hours = int((recent_start - baseline_start).total_seconds() / 3600) or 1
    warnings: List[dict] = []
    for check_id, recent_cnt in recent_total.items():
        if recent_cnt < min_events:
            continue
        base_total = baseline_total.get(check_id, 0.0)
        base_mean = base_total / baseline_hours
        counts = baseline_counts.get(check_id, {})
        missing = max(0, baseline_hours - len(counts))
        sum_sq = 0.0
        for val in counts.values():
            sum_sq += (val - base_mean) ** 2
        if missing:
            sum_sq += missing * ((0.0 - base_mean) ** 2)
        std = math.sqrt(sum_sq / baseline_hours) if baseline_hours > 0 else 0.0

        recent_rate = recent_cnt / float(recent_hours)
        ratio = (recent_rate / base_mean) if base_mean > 0 else None
        zscore = ((recent_rate - base_mean) / std) if std > 0 else None
        signal = "rate_spike"
        if base_mean == 0 and recent_cnt >= min_events:
            signal = "new_spike"
        severity = "low"
        if (zscore is not None and zscore >= 3.0) or (ratio is not None and ratio >= 3.0) or signal == "new_spike":
            severity = "high"
        elif (zscore is not None and zscore >= z_threshold) or (ratio is not None and ratio >= ratio_threshold):
            severity = "medium"
        else:
            continue

        sigs = recent_sig.get(check_id, {})
        top_sig = None
        if sigs:
            top_sig = sorted(sigs.items(), key=lambda kv: kv[1], reverse=True)[0][0]

        chk = check_map.get(check_id)
        warnings.append({
            "check_id": check_id,
            "check_name": getattr(chk, "name", None),
            "status": getattr(chk, "status", None),
            "consecutive_failures": getattr(chk, "consecutive_failures", None),
            "signal": signal,
            "severity": severity,
            "recent_count": recent_cnt,
            "recent_rate_per_hour": round(recent_rate, 4),
            "baseline_mean_per_hour": round(base_mean, 4),
            "baseline_std_per_hour": round(std, 4),
            "ratio": round(ratio, 4) if ratio is not None else None,
            "zscore": round(zscore, 4) if zscore is not None else None,
            "top_signature": top_sig,
        })

    warnings.sort(key=lambda r: (r["severity"] == "high", r["recent_count"]), reverse=True)
    return {
        "project_id": project_id,
        "baseline_start": baseline_start.isoformat(),
        "recent_start": recent_start.isoformat(),
        "recent_hours": recent_hours,
        "baseline_days": baseline_days,
        "z_threshold": z_threshold,
        "ratio_threshold": ratio_threshold,
        "warnings": warnings,
    }


@router.get("/analytics/predictive")
def predictive_alerts(
    project_id: int = Path(..., ge=1),
    recent_hours: int = Query(24, ge=6, le=168),
    min_events: int = Query(3, ge=1, le=200),
    ratio_threshold: float = Query(2.0, ge=1.1, le=20.0),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Forecast next-hour failure volume using a simple linear trend."""
    now = datetime.utcnow()
    start = now - timedelta(hours=recent_hours)
    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= start,
        Event.created_at <= now,
    )
    events = session.exec(stmt).all()

    buckets: Dict[int, Dict[str, float]] = {}
    for ev in events:
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        hour = ev.created_at.replace(minute=0, second=0, microsecond=0).isoformat()
        check_bucket = buckets.setdefault(ev.check_id, {})
        check_bucket[hour] = check_bucket.get(hour, 0.0) + weight

    warnings: List[dict] = []
    for check_id, counts in buckets.items():
        series_hours = []
        for i in range(recent_hours):
            h = (start + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0).isoformat()
            series_hours.append(counts.get(h, 0.0))
        if sum(series_hours) < min_events:
            continue
        slope, intercept, next_val = _linear_forecast(series_hours)
        base_mean = sum(series_hours) / len(series_hours)
        ratio = (next_val / base_mean) if base_mean > 0 else None
        if next_val >= min_events and ratio is not None and ratio >= ratio_threshold and slope > 0:
            warnings.append({
                "check_id": check_id,
                "recent_hours": recent_hours,
                "recent_total": round(sum(series_hours), 4),
                "baseline_mean_per_hour": round(base_mean, 4),
                "trend_slope_per_hour": round(slope, 4),
                "predicted_next_hour": round(next_val, 4),
                "ratio": round(ratio, 4),
            })

    warnings.sort(key=lambda r: (r["predicted_next_hour"], r["trend_slope_per_hour"]), reverse=True)
    return {
        "project_id": project_id,
        "recent_start": start.isoformat(),
        "recent_end": now.isoformat(),
        "recent_hours": recent_hours,
        "ratio_threshold": ratio_threshold,
        "warnings": warnings,
    }

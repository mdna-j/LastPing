from datetime import datetime, timedelta
from typing import Optional, Dict, List
import json
import re
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from pydantic import BaseModel, conint
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Check, Project, Incident, PredictiveModel, PredictiveModelQuality, Anomaly
from ..analytics_ml import find_similar_incidents, cluster_incidents
from ..predictive_models import (
    MODEL_TYPE_SEASONAL,
    evaluate_predictive_model_quality,
    list_active_models,
    predictive_warnings_from_models,
    train_seasonal_hourly_models,
)
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


def _compute_failure_trends(
    session: Session,
    project_id: int,
    start_dt: datetime,
    end_dt: datetime,
    interval: str,
) -> dict:
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
    return {
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "interval": interval,
        "series": series,
    }


class PredictiveTrainIn(BaseModel):
    check_id: Optional[int] = None
    days: conint(ge=1, le=365) = 30
    min_events: conint(ge=1, le=10000) = 10
    model_type: str = MODEL_TYPE_SEASONAL


class PredictiveQualityRunIn(BaseModel):
    hours: conint(ge=6, le=720) = 48
    min_samples: conint(ge=1, le=720) = 24
    drift_ratio_threshold: float = 2.0
    mae_threshold: float = 1.0
    model_type: str = MODEL_TYPE_SEASONAL


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
    return _compute_failure_trends(session, project_id, start_dt, end_dt, interval)


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
    z_threshold: float = Query(2.0, ge=0.5, le=10.0),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Forecast next-hour failure volume using a simple linear trend."""
    now = datetime.utcnow()

    models = list_active_models(session, project_id)
    if models:
        warnings = predictive_warnings_from_models(
            session=session,
            project_id=project_id,
            now=now,
            recent_hours=recent_hours,
            min_events=min_events,
            z_threshold=z_threshold,
        )
        return {
            "project_id": project_id,
            "recent_start": (now - timedelta(hours=recent_hours)).isoformat(),
            "recent_end": now.isoformat(),
            "recent_hours": recent_hours,
            "model_used": True,
            "model_type": MODEL_TYPE_SEASONAL,
            "model_count": len(models),
            "warnings": warnings,
        }

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
        "model_used": False,
        "warnings": warnings,
    }


@router.post("/analytics/predictive/train")
def train_predictive_models(
    project_id: int = Path(..., ge=1),
    payload: PredictiveTrainIn = Body(...),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    if payload.model_type != MODEL_TYPE_SEASONAL:
        raise HTTPException(status_code=400, detail="unsupported model_type")
    if payload.check_id is not None:
        chk = session.get(Check, payload.check_id)
        if not chk or chk.project_id != project_id:
            raise HTTPException(status_code=404, detail="check not found")
    models = train_seasonal_hourly_models(
        session=session,
        project_id=project_id,
        check_id=payload.check_id,
        days=payload.days,
        min_events=payload.min_events,
        model_type=payload.model_type,
    )
    return {
        "project_id": project_id,
        "trained": len(models),
        "models": [
            {
                "id": m.id,
                "check_id": m.check_id,
                "model_type": m.model_type,
                "version": m.version,
                "trained_at": m.trained_at.isoformat(),
                "window_start": m.window_start.isoformat() if m.window_start else None,
                "window_end": m.window_end.isoformat() if m.window_end else None,
            }
            for m in models
        ],
    }


@router.get("/analytics/predictive/models")
def list_predictive_models(
    project_id: int = Path(..., ge=1),
    check_id: Optional[int] = Query(None, ge=1),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    models = session.exec(
        select(PredictiveModel).where(
            PredictiveModel.project_id == project_id,
            PredictiveModel.active == True,
            PredictiveModel.model_type == MODEL_TYPE_SEASONAL,
        )
    ).all()
    if check_id is not None:
        models = [m for m in models if m.check_id == check_id]
    return {
        "project_id": project_id,
        "count": len(models),
        "models": [
            {
                "id": m.id,
                "check_id": m.check_id,
                "model_type": m.model_type,
                "version": m.version,
                "trained_at": m.trained_at.isoformat(),
                "window_start": m.window_start.isoformat() if m.window_start else None,
                "window_end": m.window_end.isoformat() if m.window_end else None,
                "active": m.active,
                "metrics_json": m.metrics_json,
            }
            for m in models
        ],
    }


@router.post("/analytics/predictive/quality/run")
def run_predictive_quality_monitoring(
    project_id: int = Path(..., ge=1),
    payload: PredictiveQualityRunIn = Body(...),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    if payload.model_type != MODEL_TYPE_SEASONAL:
        raise HTTPException(status_code=400, detail="unsupported model_type")
    rows = evaluate_predictive_model_quality(
        session=session,
        project_id=project_id,
        hours=payload.hours,
        min_samples=payload.min_samples,
        drift_ratio_threshold=payload.drift_ratio_threshold,
        mae_threshold=payload.mae_threshold,
        model_type=payload.model_type,
    )
    drifted = [r for r in rows if r.status == "drift"]
    insufficient = [r for r in rows if r.status == "insufficient_data"]
    return {
        "project_id": project_id,
        "evaluated": len(rows),
        "drifted": len(drifted),
        "insufficient_data": len(insufficient),
        "ok": len(rows) - len(drifted) - len(insufficient),
        "rows": [
            {
                "id": r.id,
                "predictive_model_id": r.predictive_model_id,
                "check_id": r.check_id,
                "status": r.status,
                "sample_count": r.sample_count,
                "mae": r.mae,
                "rmse": r.rmse,
                "mape": r.mape,
                "drift_ratio": r.drift_ratio,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/analytics/predictive/quality")
def list_predictive_quality(
    project_id: int = Path(..., ge=1),
    check_id: Optional[int] = Query(None, ge=1),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    stmt = select(PredictiveModelQuality).where(
        PredictiveModelQuality.project_id == project_id
    )
    if check_id is not None:
        stmt = stmt.where(PredictiveModelQuality.check_id == check_id)
    if status:
        stmt = stmt.where(PredictiveModelQuality.status == status)
    rows = session.exec(
        stmt.order_by(PredictiveModelQuality.created_at.desc()).limit(limit)
    ).all()
    return {
        "project_id": project_id,
        "count": len(rows),
        "rows": [
            {
                "id": r.id,
                "predictive_model_id": r.predictive_model_id,
                "check_id": r.check_id,
                "status": r.status,
                "sample_count": r.sample_count,
                "mae": r.mae,
                "rmse": r.rmse,
                "mape": r.mape,
                "drift_ratio": r.drift_ratio,
                "metrics_json": r.metrics_json,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/analytics/anomalies")
def anomaly_predictions(
    project_id: int = Path(..., ge=1),
    recent_hours: int = Query(24, ge=6, le=168),
    min_events: int = Query(3, ge=1, le=200),
    z_threshold: float = Query(2.0, ge=0.5, le=10.0),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Detect anomalies using recent hourly event rates and a simple forecast."""
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
        series = []
        for i in range(recent_hours):
            h = (start + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0).isoformat()
            series.append(counts.get(h, 0.0))
        if sum(series) < min_events:
            continue
        slope, _intercept, next_val = _linear_forecast(series)
        mean = sum(series) / len(series)
        var = sum((v - mean) ** 2 for v in series) / len(series)
        std = math.sqrt(var) if var > 0 else 0.0
        score = ((next_val - mean) / std) if std > 0 else None
        if score is not None and score >= z_threshold and next_val >= min_events:
            warnings.append({
                "check_id": check_id,
                "recent_hours": recent_hours,
                "mean_per_hour": round(mean, 4),
                "std_per_hour": round(std, 4),
                "trend_slope_per_hour": round(slope, 4),
                "predicted_next_hour": round(next_val, 4),
                "anomaly_score": round(score, 4),
            })

    warnings.sort(key=lambda r: (r["anomaly_score"], r["predicted_next_hour"]), reverse=True)
    return {
        "project_id": project_id,
        "recent_start": start.isoformat(),
        "recent_end": now.isoformat(),
        "recent_hours": recent_hours,
        "z_threshold": z_threshold,
        "warnings": warnings,
    }


@router.get("/analytics/anomaly-events")
def list_persisted_anomalies(
    project_id: int = Path(..., ge=1),
    check_id: Optional[int] = Query(None, ge=1),
    incident_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """List persisted anomaly rows for debugging by check and/or incident."""
    project_check_ids = session.exec(select(Check.id).where(Check.project_id == project_id)).all()
    if not project_check_ids:
        return {"project_id": project_id, "count": 0, "anomalies": []}

    if check_id is not None and check_id not in project_check_ids:
        raise HTTPException(status_code=404, detail="Check not found")
    if incident_id is not None:
        inc = session.get(Incident, incident_id)
        if not inc or inc.project_id != project_id:
            raise HTTPException(status_code=404, detail="Incident not found")

    stmt = select(Anomaly).where(Anomaly.check_id.in_(project_check_ids))
    if check_id is not None:
        stmt = stmt.where(Anomaly.check_id == check_id)
    if incident_id is not None:
        stmt = stmt.where(Anomaly.incident_id == incident_id)

    rows = session.exec(stmt.order_by(Anomaly.created_at.desc()).limit(limit)).all()
    out = []
    for row in rows:
        evidence = {}
        if row.evidence_json:
            try:
                evidence = json.loads(row.evidence_json)
            except Exception:
                evidence = {"raw": row.evidence_json}
        out.append(
            {
                "id": row.id,
                "check_id": row.check_id,
                "incident_id": row.incident_id,
                "type": row.type,
                "severity": row.severity,
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
                "evidence": evidence,
                "created_at": row.created_at.isoformat(),
            }
        )

    return {"project_id": project_id, "count": len(out), "anomalies": out}


@router.get("/analytics/incident-clusters")
def incident_clusters(
    project_id: int = Path(..., ge=1),
    days: int = Query(90, ge=1, le=365),
    threshold: float = Query(0.35, ge=0.0, le=1.0),
    min_cluster_size: int = Query(2, ge=2, le=100),
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    """Cluster incidents using TF-IDF similarity."""
    clusters = cluster_incidents(
        session=session,
        project_id=project_id,
        days=days,
        threshold=threshold,
        min_cluster_size=min_cluster_size,
        limit=limit,
    )
    return {
        "project_id": project_id,
        "days": days,
        "threshold": threshold,
        "min_cluster_size": min_cluster_size,
        "clusters": clusters,
    }

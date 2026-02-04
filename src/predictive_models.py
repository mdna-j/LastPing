import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlmodel import Session, select

from .models import Event, PredictiveModel

MODEL_TYPE_SEASONAL = "seasonal_hourly_v1"


def _bucket_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _event_weight(event_type: str) -> float:
    if event_type in ("down", "http_failure"):
        return 1.0
    if event_type == "degraded":
        return 0.5
    return 0.0


def _linear_forecast(values: List[float]) -> tuple[float, float, float]:
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


def _hour_series(start: datetime, hours: int, counts: Dict[str, float]) -> List[float]:
    series = []
    for i in range(hours):
        h = (start + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0).isoformat()
        series.append(counts.get(h, 0.0))
    return series


def list_active_models(
    session: Session,
    project_id: int,
    check_id: Optional[int] = None,
    model_type: str = MODEL_TYPE_SEASONAL,
) -> List[PredictiveModel]:
    stmt = select(PredictiveModel).where(
        PredictiveModel.project_id == project_id,
        PredictiveModel.active == True,
        PredictiveModel.model_type == model_type,
    )
    if check_id is not None:
        stmt = stmt.where(PredictiveModel.check_id == check_id)
    return session.exec(stmt).all()


def train_seasonal_hourly_models(
    session: Session,
    project_id: int,
    check_id: Optional[int] = None,
    days: int = 30,
    min_events: int = 10,
    model_type: str = MODEL_TYPE_SEASONAL,
) -> List[PredictiveModel]:
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= start,
        Event.created_at <= end,
    )
    if check_id is not None:
        stmt = stmt.where(Event.check_id == check_id)
    events = session.exec(stmt).all()

    buckets: Dict[int, Dict[str, float]] = {}
    for ev in events:
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        hour = _bucket_hour(ev.created_at).isoformat()
        check_bucket = buckets.setdefault(ev.check_id, {})
        check_bucket[hour] = check_bucket.get(hour, 0.0) + weight

    hours = int((end - start).total_seconds() / 3600) or 1
    models: List[PredictiveModel] = []

    for cid, counts in buckets.items():
        series = _hour_series(start, hours, counts)
        total_events = sum(series)
        if total_events < min_events:
            continue

        hourly_values: Dict[int, List[float]] = {h: [] for h in range(24)}
        for i, val in enumerate(series):
            hod = (start + timedelta(hours=i)).hour
            hourly_values[hod].append(val)

        hourly_mean = []
        hourly_std = []
        for h in range(24):
            vals = hourly_values[h]
            if not vals:
                hourly_mean.append(0.0)
                hourly_std.append(0.0)
                continue
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            hourly_mean.append(mean)
            hourly_std.append(math.sqrt(var) if var > 0 else 0.0)

        global_mean = sum(series) / len(series)
        global_var = sum((v - global_mean) ** 2 for v in series) / len(series)
        global_std = math.sqrt(global_var) if global_var > 0 else 0.0

        params = {
            "hourly_mean": hourly_mean,
            "hourly_std": hourly_std,
            "global_mean": global_mean,
            "global_std": global_std,
        }
        metrics = {
            "samples": len(series),
            "event_total": total_events,
            "window_days": days,
        }

        # deactivate existing models
        existing = session.exec(
            select(PredictiveModel).where(
                PredictiveModel.project_id == project_id,
                PredictiveModel.check_id == cid,
                PredictiveModel.model_type == model_type,
                PredictiveModel.active == True,
            )
        ).all()
        for m in existing:
            m.active = False
            session.add(m)

        latest = session.exec(
            select(PredictiveModel)
            .where(
                PredictiveModel.project_id == project_id,
                PredictiveModel.check_id == cid,
                PredictiveModel.model_type == model_type,
            )
            .order_by(PredictiveModel.version.desc())
        ).first()
        version = (latest.version + 1) if latest else 1

        model = PredictiveModel(
            project_id=project_id,
            check_id=cid,
            model_type=model_type,
            version=version,
            trained_at=end,
            window_start=start,
            window_end=end,
            params_json=json.dumps(params),
            metrics_json=json.dumps(metrics),
            active=True,
        )
        session.add(model)
        models.append(model)

    session.commit()
    for m in models:
        session.refresh(m)
    return models


def predictive_warnings_from_models(
    session: Session,
    project_id: int,
    now: datetime,
    recent_hours: int = 24,
    min_events: int = 3,
    z_threshold: float = 2.0,
    model_type: str = MODEL_TYPE_SEASONAL,
) -> List[dict]:
    models = list_active_models(session, project_id, model_type=model_type)
    if not models:
        return []

    start = now - timedelta(hours=recent_hours)
    check_ids = [m.check_id for m in models if m.check_id is not None]
    if not check_ids:
        return []

    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= start,
        Event.created_at <= now,
        Event.check_id.in_(check_ids),
    )
    events = session.exec(stmt).all()

    buckets: Dict[int, Dict[str, float]] = {}
    for ev in events:
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        hour = _bucket_hour(ev.created_at).isoformat()
        check_bucket = buckets.setdefault(ev.check_id, {})
        check_bucket[hour] = check_bucket.get(hour, 0.0) + weight

    warnings: List[dict] = []
    next_hour = (now + timedelta(hours=1)).hour
    for model in models:
        cid = model.check_id
        if cid is None:
            continue
        series = _hour_series(start, recent_hours, buckets.get(cid, {}))
        if sum(series) < min_events:
            continue
        slope, _intercept, _next_val = _linear_forecast(series)
        try:
            params = json.loads(model.params_json or "{}")
        except Exception:
            params = {}
        means = params.get("hourly_mean") or []
        stds = params.get("hourly_std") or []
        mean = means[next_hour] if len(means) == 24 else params.get("global_mean", 0.0)
        std = stds[next_hour] if len(stds) == 24 and stds[next_hour] > 0 else params.get("global_std", 0.0)
        predicted = max(0.0, mean + max(0.0, slope))
        zscore = ((predicted - mean) / std) if std and std > 0 else None

        if zscore is not None and zscore >= z_threshold and predicted >= min_events:
            warnings.append({
                "check_id": cid,
                "model_id": model.id,
                "model_version": model.version,
                "model_type": model.model_type,
                "recent_hours": recent_hours,
                "predicted_next_hour": round(predicted, 4),
                "expected_mean": round(mean, 4),
                "expected_std": round(std, 4),
                "trend_slope_per_hour": round(slope, 4),
                "zscore": round(zscore, 4),
            })

    warnings.sort(key=lambda r: (r["zscore"], r["predicted_next_hour"]), reverse=True)
    return warnings

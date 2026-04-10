import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check, Project
from ..runtime_metrics import snapshot_request_metrics, snapshot_traces
from .ui import _build_platform_observability


router = APIRouter(prefix="/observability", tags=["observability"])

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_STATE_VALUES = {
    "neutral": 0,
    "healthy": 1,
    "warning": 2,
    "critical": 3,
}


def _require_admin_token(x_admin_token: Optional[str]) -> None:
    expected = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Observability export requires ADMIN_TOKEN")
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="Missing admin token")
    if x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _metric_labels(**labels: object) -> str:
    parts = []
    for key, value in labels.items():
        if value is None:
            continue
        rendered = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{key}="{rendered}"')
    return "{" + ",".join(parts) + "}" if parts else ""


def _metric_line(name: str, value: object, **labels: object) -> str:
    if value is None:
        value = 0
    if isinstance(value, bool):
        value = 1 if value else 0
    return f"{name}{_metric_labels(**labels)} {value}"


def _append_metric(lines: list[str], name: str, help_text: str, metric_type: str, *samples: str) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    lines.extend(samples)


def _service_name() -> str:
    return (os.environ.get("OTEL_SERVICE_NAME") or os.environ.get("LASTPING_SERVICE_NAME") or "lastping-api").strip()


def _project_observability_rows(session: Session, project_id: Optional[int]) -> list[tuple[Project, dict]]:
    if project_id is not None:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
        return [(project, _build_platform_observability(session, project.id, checks, datetime.utcnow()))]

    projects = session.exec(select(Project).order_by(Project.id.asc())).all()
    rows: list[tuple[Project, dict]] = []
    for project in projects:
        checks = session.exec(select(Check).where(Check.project_id == project.id)).all()
        rows.append((project, _build_platform_observability(session, project.id, checks, datetime.utcnow())))
    return rows


@router.get("/prometheus", response_class=PlainTextResponse)
def prometheus_export(
    project_id: Optional[int] = Query(None, ge=1),
    window_seconds: int = Query(300, ge=1, le=3600),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    _require_admin_token(x_admin_token)

    request_metrics = snapshot_request_metrics(window_seconds=window_seconds)
    lines: list[str] = []
    _append_metric(
        lines,
        "lastping_runtime_info",
        "Static runtime info for the LastPing API process.",
        "gauge",
        _metric_line("lastping_runtime_info", 1, service=_service_name()),
    )
    projects_total_line_index = len(lines) + 2
    _append_metric(lines, "lastping_projects_total", "Number of projects included in this export.", "gauge", _metric_line("lastping_projects_total", 0))
    _append_metric(
        lines,
        "lastping_api_requests_window_total",
        "HTTP requests observed in the recent runtime window.",
        "gauge",
        _metric_line(
            "lastping_api_requests_window_total",
            request_metrics["request_count"],
            window_seconds=window_seconds,
        ),
    )
    _append_metric(
        lines,
        "lastping_api_request_avg_ms",
        "Average HTTP request duration in milliseconds for the recent runtime window.",
        "gauge",
        _metric_line(
            "lastping_api_request_avg_ms",
            round(float(request_metrics["avg_ms"] or 0.0), 3),
            window_seconds=window_seconds,
        ),
    )
    _append_metric(
        lines,
        "lastping_api_request_p95_ms",
        "95th percentile HTTP request duration in milliseconds for the recent runtime window.",
        "gauge",
        _metric_line(
            "lastping_api_request_p95_ms",
            round(float(request_metrics["p95_ms"] or 0.0), 3),
            window_seconds=window_seconds,
        ),
    )
    _append_metric(
        lines,
        "lastping_api_request_error_rate",
        "Fraction of recent HTTP requests returning 5xx responses.",
        "gauge",
        _metric_line(
            "lastping_api_request_error_rate",
            round(float(request_metrics["error_rate"] or 0.0), 6),
            window_seconds=window_seconds,
        ),
    )
    if request_metrics["paths"]:
        _append_metric(
            lines,
            "lastping_api_request_path_total",
            "Per-path HTTP request counts in the recent runtime window.",
            "gauge",
            *[
                _metric_line(
                    "lastping_api_request_path_total",
                    path_row["count"],
                    window_seconds=window_seconds,
                    path=path_row["path"],
                )
                for path_row in request_metrics["paths"]
            ],
        )

    rows = _project_observability_rows(session, project_id)
    lines[projects_total_line_index] = _metric_line("lastping_projects_total", len(rows))

    worker_scheduled_samples: list[str] = []
    worker_overdue_samples: list[str] = []
    worker_max_overdue_samples: list[str] = []
    worker_state_samples: list[str] = []
    queue_open_samples: list[str] = []
    queue_pending_samples: list[str] = []
    queue_oldest_samples: list[str] = []
    queue_state_samples: list[str] = []
    retention_lag_samples: list[str] = []
    retention_truncated_samples: list[str] = []
    retention_state_samples: list[str] = []
    failure_total_samples: list[str] = []
    failure_channel_samples: list[str] = []
    failure_state_samples: list[str] = []
    model_active_samples: list[str] = []
    model_drifted_samples: list[str] = []
    model_insufficient_samples: list[str] = []
    model_stale_samples: list[str] = []
    model_state_samples: list[str] = []
    api_state_samples: list[str] = []

    for project, platform in rows:
        base_labels = {"project_id": project.id, "project_name": project.name}
        worker = platform["worker_lag"]
        queue = platform["queue_health"]
        retention = platform["retention"]
        failures = platform["failed_notifications"]
        model_ops = platform["model_ops"]
        api_latency = platform["api_latency"]

        worker_scheduled_samples.append(_metric_line("lastping_project_worker_scheduled_checks", worker["scheduled_checks"], **base_labels))
        worker_overdue_samples.append(_metric_line("lastping_project_worker_overdue_checks", worker["overdue_checks"], **base_labels))
        worker_max_overdue_samples.append(_metric_line("lastping_project_worker_max_overdue_seconds", worker["max_overdue_seconds"] or 0, **base_labels))
        worker_state_samples.append(_metric_line("lastping_project_worker_state", _STATE_VALUES.get(worker["state"], 0), state=worker["state"], **base_labels))
        queue_open_samples.append(_metric_line("lastping_project_queue_open_oncall_alerts", queue["open_oncall_alerts"], **base_labels))
        queue_pending_samples.append(_metric_line("lastping_project_queue_pending_approvals", queue["pending_approvals"], **base_labels))
        queue_oldest_samples.append(_metric_line("lastping_project_queue_oldest_open_seconds", queue["oldest_open_seconds"] or 0, **base_labels))
        queue_state_samples.append(_metric_line("lastping_project_queue_state", _STATE_VALUES.get(queue["state"], 0), state=queue["state"], **base_labels))
        retention_lag_samples.append(_metric_line("lastping_project_retention_lag_seconds", retention["lag_seconds"] or 0, **base_labels))
        retention_truncated_samples.append(_metric_line("lastping_project_retention_truncated_tables", len(retention["truncated_tables"]), **base_labels))
        retention_state_samples.append(_metric_line("lastping_project_retention_state", _STATE_VALUES.get(retention["state"], 0), state=retention["state"], **base_labels))
        failure_total_samples.append(_metric_line("lastping_project_failed_notifications_24h", failures["failures_24h"], **base_labels))
        failure_state_samples.append(_metric_line("lastping_project_failed_notification_state", _STATE_VALUES.get(failures["state"], 0), state=failures["state"], **base_labels))
        for channel, count in sorted(failures["channels"].items()):
            failure_channel_samples.append(_metric_line("lastping_project_failed_notifications_channel_24h", count, channel=channel, **base_labels))
        model_active_samples.append(_metric_line("lastping_project_model_active_models", model_ops["active_models"], **base_labels))
        model_drifted_samples.append(_metric_line("lastping_project_model_drifted_models", model_ops["drifted_models"], **base_labels))
        model_insufficient_samples.append(_metric_line("lastping_project_model_insufficient_models", model_ops["insufficient_models"], **base_labels))
        model_stale_samples.append(_metric_line("lastping_project_model_stale", 1 if model_ops["stale"] else 0, **base_labels))
        model_state_samples.append(_metric_line("lastping_project_model_state", _STATE_VALUES.get(model_ops["state"], 0), state=model_ops["state"], **base_labels))
        api_state_samples.append(
            _metric_line(
                "lastping_project_api_latency_state",
                _STATE_VALUES.get(api_latency["state"], 0),
                state=api_latency["state"],
                **base_labels,
            )
        )

    if worker_scheduled_samples:
        _append_metric(lines, "lastping_project_worker_scheduled_checks", "Number of scheduled polling checks for a project.", "gauge", *worker_scheduled_samples)
        _append_metric(lines, "lastping_project_worker_overdue_checks", "Number of overdue polling checks for a project.", "gauge", *worker_overdue_samples)
        _append_metric(lines, "lastping_project_worker_max_overdue_seconds", "Maximum overdue seconds across project checks.", "gauge", *worker_max_overdue_samples)
        _append_metric(lines, "lastping_project_worker_state", "Derived worker lag state for the project health surface.", "gauge", *worker_state_samples)
    if queue_open_samples:
        _append_metric(lines, "lastping_project_queue_open_oncall_alerts", "Open on-call alerts for a project.", "gauge", *queue_open_samples)
        _append_metric(lines, "lastping_project_queue_pending_approvals", "Pending remediation approvals for a project.", "gauge", *queue_pending_samples)
        _append_metric(lines, "lastping_project_queue_oldest_open_seconds", "Oldest open queue item age in seconds.", "gauge", *queue_oldest_samples)
        _append_metric(lines, "lastping_project_queue_state", "Derived queue state for the project health surface.", "gauge", *queue_state_samples)
    if retention_lag_samples:
        _append_metric(lines, "lastping_project_retention_lag_seconds", "Raw retention lag in seconds for a project.", "gauge", *retention_lag_samples)
        _append_metric(lines, "lastping_project_retention_truncated_tables", "Count of retention tables truncated in the latest prune run.", "gauge", *retention_truncated_samples)
        _append_metric(lines, "lastping_project_retention_state", "Derived retention state for the project health surface.", "gauge", *retention_state_samples)
    if failure_total_samples:
        _append_metric(lines, "lastping_project_failed_notifications_24h", "Failed notifications over the last 24 hours for a project.", "gauge", *failure_total_samples)
        if failure_channel_samples:
            _append_metric(lines, "lastping_project_failed_notifications_channel_24h", "Failed notifications over 24 hours broken down by channel.", "gauge", *failure_channel_samples)
        _append_metric(lines, "lastping_project_failed_notification_state", "Derived failed-notification state for the project health surface.", "gauge", *failure_state_samples)
    if model_active_samples:
        _append_metric(lines, "lastping_project_model_active_models", "Active predictive models for a project.", "gauge", *model_active_samples)
        _append_metric(lines, "lastping_project_model_drifted_models", "Drifted predictive models for a project.", "gauge", *model_drifted_samples)
        _append_metric(lines, "lastping_project_model_insufficient_models", "Predictive models with insufficient data for a project.", "gauge", *model_insufficient_samples)
        _append_metric(lines, "lastping_project_model_stale", "Whether the latest predictive model quality data is stale.", "gauge", *model_stale_samples)
        _append_metric(lines, "lastping_project_model_state", "Derived predictive model state for the project health surface.", "gauge", *model_state_samples)
    if api_state_samples:
        _append_metric(lines, "lastping_project_api_latency_state", "Derived API latency state for the project health surface.", "gauge", *api_state_samples)

    return PlainTextResponse("\n".join(lines) + "\n", media_type=_PROMETHEUS_CONTENT_TYPE)


@router.get("/otel/traces")
def otel_trace_export(
    window_seconds: int = Query(900, ge=1, le=3600),
    limit: int = Query(100, ge=1, le=500),
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin_token(x_admin_token)
    trace_snapshot = snapshot_traces(window_seconds=window_seconds, limit=limit)
    spans = []
    for trace in trace_snapshot["traces"]:
        started_at = datetime.fromisoformat(trace["started_at"])
        ended_at = datetime.fromisoformat(trace["recorded_at"])
        spans.append(
            {
                "traceId": trace["trace_id"],
                "spanId": trace["span_id"],
                "parentSpanId": trace["parent_span_id"] or "",
                "name": f'{trace["method"]} {trace["path"]}',
                "kind": 2,
                "startTimeUnixNano": str(int(started_at.timestamp() * 1_000_000_000)),
                "endTimeUnixNano": str(int(ended_at.timestamp() * 1_000_000_000)),
                "attributes": [
                    {"key": "http.method", "value": {"stringValue": trace["method"]}},
                    {"key": "http.route", "value": {"stringValue": trace["path"]}},
                    {"key": "http.status_code", "value": {"intValue": trace["status_code"]}},
                    {"key": "lastping.duration_ms", "value": {"doubleValue": round(float(trace["duration_ms"]), 3)}},
                ],
                "status": {"code": 2 if int(trace["status_code"]) >= 500 else 1},
            }
        )

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": _service_name()}},
                        {"key": "service.namespace", "value": {"stringValue": "lastping"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "lastping.runtime", "version": "1"},
                        "spans": spans,
                    }
                ],
            }
        ],
        "window_seconds": trace_snapshot["window_seconds"],
        "trace_count": trace_snapshot["trace_count"],
        "latest_at": trace_snapshot["latest_at"],
    }
    return JSONResponse(payload)

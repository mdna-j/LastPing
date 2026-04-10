from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import secrets
from threading import Lock
from typing import Deque, Dict, List, Optional


_REQUEST_WINDOW_SECONDS = 300
_TRACE_WINDOW_SECONDS = 900
_REQUEST_SAMPLES: Deque[dict] = deque()
_TRACE_SAMPLES: Deque[dict] = deque()
_REQUEST_LOCK = Lock()
_TRACE_LOCK = Lock()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _trim_locked(now: datetime) -> None:
    cutoff = now - timedelta(seconds=_REQUEST_WINDOW_SECONDS)
    while _REQUEST_SAMPLES and _REQUEST_SAMPLES[0]["recorded_at"] < cutoff:
        _REQUEST_SAMPLES.popleft()


def _trim_trace_locked(now: datetime) -> None:
    cutoff = now - timedelta(seconds=_TRACE_WINDOW_SECONDS)
    while _TRACE_SAMPLES and _TRACE_SAMPLES[0]["recorded_at"] < cutoff:
        _TRACE_SAMPLES.popleft()


def should_track_request(path: str) -> bool:
    if not path:
        return False
    if path.startswith("/static"):
        return False
    if path.startswith("/observability"):
        return False
    if path == "/":
        return False
    if path.startswith("/ui/") and not path.endswith("/health") and not path.endswith("/data"):
        return False
    return True


def _normalize_hex(value: str, length: int) -> Optional[str]:
    text = str(value or "").strip().lower()
    if len(text) != length:
        return None
    if set(text) == {"0"}:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text


def parse_traceparent(header_value: Optional[str]) -> Optional[dict]:
    raw = str(header_value or "").strip()
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) != 4:
        return None
    version, trace_id, parent_span_id, trace_flags = parts
    if _normalize_hex(version, 2) is None and version != "00":
        return None
    normalized_trace_id = _normalize_hex(trace_id, 32)
    normalized_parent_span_id = _normalize_hex(parent_span_id, 16)
    normalized_trace_flags = _normalize_hex(trace_flags, 2)
    if not normalized_trace_id or not normalized_parent_span_id or not normalized_trace_flags:
        return None
    return {
        "version": version.lower(),
        "trace_id": normalized_trace_id,
        "parent_span_id": normalized_parent_span_id,
        "trace_flags": normalized_trace_flags,
    }


def generate_trace_context(traceparent: Optional[str] = None) -> dict:
    parsed = parse_traceparent(traceparent)
    if parsed:
        trace_id = parsed["trace_id"]
        parent_span_id = parsed["parent_span_id"]
        trace_flags = parsed["trace_flags"]
    else:
        trace_id = secrets.token_hex(16)
        parent_span_id = None
        trace_flags = "01"
    span_id = secrets.token_hex(8)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "trace_flags": trace_flags,
    }


def format_traceparent(trace_id: str, span_id: str, trace_flags: str = "01") -> str:
    return f"00-{trace_id}-{span_id}-{trace_flags}"


def record_request(path: str, method: str, status_code: int, duration_ms: float) -> None:
    if not should_track_request(path):
        return
    now = _utcnow()
    sample = {
        "path": path,
        "method": method,
        "status_code": int(status_code),
        "duration_ms": max(float(duration_ms), 0.0),
        "recorded_at": now,
    }
    with _REQUEST_LOCK:
        _trim_locked(now)
        _REQUEST_SAMPLES.append(sample)


def record_trace(
    path: str,
    method: str,
    status_code: int,
    duration_ms: float,
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: Optional[str],
    started_at: datetime,
) -> None:
    if not should_track_request(path):
        return
    now = _utcnow()
    sample = {
        "path": path,
        "method": method,
        "status_code": int(status_code),
        "duration_ms": max(float(duration_ms), 0.0),
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "started_at": started_at,
        "recorded_at": now,
    }
    with _TRACE_LOCK:
        _trim_trace_locked(now)
        _TRACE_SAMPLES.append(sample)


def snapshot_request_metrics(window_seconds: int = _REQUEST_WINDOW_SECONDS) -> Dict[str, object]:
    now = _utcnow()
    window_seconds = max(int(window_seconds), 1)
    cutoff = now - timedelta(seconds=window_seconds)
    with _REQUEST_LOCK:
        _trim_locked(now)
        rows: List[dict] = [row for row in _REQUEST_SAMPLES if row["recorded_at"] >= cutoff]

    if not rows:
        return {
            "window_seconds": window_seconds,
            "request_count": 0,
            "avg_ms": None,
            "p95_ms": None,
            "error_rate": 0.0,
            "latest_at": None,
            "paths": [],
        }

    durations = sorted(float(row["duration_ms"]) for row in rows)
    idx = max(int(round(0.95 * (len(durations) - 1))), 0)
    error_count = sum(1 for row in rows if int(row["status_code"]) >= 500)

    path_counts: Dict[str, int] = {}
    for row in rows:
        key = str(row["path"])
        path_counts[key] = path_counts.get(key, 0) + 1
    top_paths = [
        {"path": path, "count": count}
        for path, count in sorted(path_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    return {
        "window_seconds": window_seconds,
        "request_count": len(rows),
        "avg_ms": sum(durations) / len(durations),
        "p95_ms": durations[idx],
        "error_rate": error_count / len(rows),
        "latest_at": max(row["recorded_at"] for row in rows).isoformat(),
        "paths": top_paths,
    }


def snapshot_traces(window_seconds: int = _TRACE_WINDOW_SECONDS, limit: int = 100) -> Dict[str, object]:
    now = _utcnow()
    window_seconds = max(int(window_seconds), 1)
    limit = max(int(limit), 1)
    cutoff = now - timedelta(seconds=window_seconds)
    with _TRACE_LOCK:
        _trim_trace_locked(now)
        rows: List[dict] = [row for row in _TRACE_SAMPLES if row["recorded_at"] >= cutoff]

    rows.sort(key=lambda row: row["started_at"], reverse=True)
    rows = rows[:limit]
    return {
        "window_seconds": window_seconds,
        "trace_count": len(rows),
        "latest_at": rows[0]["recorded_at"].isoformat() if rows else None,
        "traces": [
            {
                "trace_id": row["trace_id"],
                "span_id": row["span_id"],
                "parent_span_id": row["parent_span_id"],
                "path": row["path"],
                "method": row["method"],
                "status_code": row["status_code"],
                "duration_ms": row["duration_ms"],
                "started_at": row["started_at"].isoformat(),
                "recorded_at": row["recorded_at"].isoformat(),
            }
            for row in rows
        ],
    }


def reset_request_metrics() -> None:
    with _REQUEST_LOCK:
        _REQUEST_SAMPLES.clear()
    with _TRACE_LOCK:
        _TRACE_SAMPLES.clear()

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Deque, Dict, List


_REQUEST_WINDOW_SECONDS = 300
_REQUEST_SAMPLES: Deque[dict] = deque()
_REQUEST_LOCK = Lock()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _trim_locked(now: datetime) -> None:
    cutoff = now - timedelta(seconds=_REQUEST_WINDOW_SECONDS)
    while _REQUEST_SAMPLES and _REQUEST_SAMPLES[0]["recorded_at"] < cutoff:
        _REQUEST_SAMPLES.popleft()


def should_track_request(path: str) -> bool:
    if not path:
        return False
    if path.startswith("/static"):
        return False
    if path == "/":
        return False
    if path.startswith("/ui/") and not path.endswith("/health") and not path.endswith("/data"):
        return False
    return True


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


def reset_request_metrics() -> None:
    with _REQUEST_LOCK:
        _REQUEST_SAMPLES.clear()

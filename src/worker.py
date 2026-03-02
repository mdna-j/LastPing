import os
import time
import logging
import json
import re
import math
import subprocess
import sys
import signal
"""
Background worker: scans checks and executes monitoring logic.

This module contains the worker loop and core detection behaviour used
to mark checks UP/DOWN, persist events, and call alerting functions.
Keep scan logic deterministic and side-effect minimal so tests remain
stable.
"""

from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error
import socket
from typing import Tuple, Optional

from sqlmodel import Session, select
from sqlalchemy import update, or_, func
from sqlalchemy.exc import IntegrityError

from .db import engine
from .models import Check, CheckType, CheckStatus, Event, EventType, Project, Incident, CheckResult
from .alerts import notify_down, notify_recovery, notify_degraded, send_email, send_sms
from .models import UptimeSnapshot, AvailabilityRollup, CheckLease, RemediationHook, RemediationLog, RemediationApproval, OnCallAlert, OnCallEscalation, OnCallRotation, OnCallMember, AuditLog, Anomaly
from .analytics_ml import find_similar_incidents

logger = logging.getLogger("lastping.worker")

# Window (seconds) in which separate failures may be grouped into a single incident
GROUP_WINDOW = int(os.environ.get("INCIDENT_GROUP_WINDOW", "600"))
_LAST_EARLY_WARNING_RUN: Optional[datetime] = None
_LAST_PREDICTIVE_RUN: Optional[datetime] = None
_LAST_ARCHIVE_RUN: Optional[datetime] = None
_LAST_QUARTERLY_RUN: Optional[datetime] = None


def _now() -> datetime:
    """Return current UTC datetime (extracted to ease testing)."""
    return datetime.utcnow()


def _as_utc_naive(value: datetime) -> datetime:
    """Normalize datetime to UTC-naive for safe comparisons across DB drivers."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _db_now(session: Session) -> datetime:
    """Return database-server UTC time when available (fallback to app clock)."""
    try:
        value = session.exec(select(func.now())).first()
        if isinstance(value, datetime):
            return _as_utc_naive(value)
    except Exception:
        logger.exception("Failed to read DB server time; falling back to app clock")
    return _as_utc_naive(_now())


def _check_run_key(check: Check, now: datetime) -> str:
    """Build a deterministic idempotency key for a single check execution slot."""
    epoch = datetime(1970, 1, 1)
    if check.type == CheckType.HEARTBEAT:
        expected = int(check.expected_interval or 600)
        grace = int(check.grace_period or 600)
        slot_seconds = max(1, expected + grace)
        slot = int((now - epoch).total_seconds() // slot_seconds)
        last_ping = getattr(check, "last_ping", None)
        last_ping_key = last_ping.isoformat() if isinstance(last_ping, datetime) else "none"
        return f"hb:{check.id}:{last_ping_key}:{slot}"

    interval = int(getattr(check, "interval", None) or 60)
    slot_seconds = max(1, interval)
    scheduled = getattr(check, "next_run", None)
    if isinstance(scheduled, datetime):
        anchor = f"next:{scheduled.isoformat()}"
    else:
        slot = int((now - epoch).total_seconds() // slot_seconds)
        anchor = f"slot:{slot}"
    return f"poll:{check.id}:{check.type}:{anchor}"


def _record_check_result(
    session: Session,
    check: Check,
    *,
    run_key: Optional[str],
    status: str,
    latency_ms: Optional[float],
    error_message: Optional[str],
    incident_id: Optional[int],
    created_at: datetime,
) -> None:
    """Persist canonical execution evidence for a check run."""
    try:
        if run_key:
            with session.no_autoflush:
                exists = session.exec(
                    select(CheckResult.id).where(
                        CheckResult.check_id == check.id,
                        CheckResult.run_key == run_key,
                    )
                ).first()
            if exists is not None:
                return
        row = CheckResult(
            check_id=check.id,
            project_id=check.project_id,
            incident_id=incident_id,
            run_key=run_key,
            status=status,
            latency_ms=latency_ms,
            error_message=error_message,
            created_at=created_at,
        )
        session.add(row)
        session.commit()
    except IntegrityError:
        # Unique (check_id, run_key) collisions are expected on retries.
        session.rollback()
    except Exception:
        session.rollback()
        logger.exception("Error recording check evidence for check %s", getattr(check, "id", None))


def _event_state(event_type: Optional[str]) -> Optional[str]:
    if event_type in (EventType.DOWN, EventType.HTTP_FAILURE):
        return CheckStatus.DOWN
    if event_type == EventType.UP:
        return CheckStatus.UP
    if event_type == EventType.DEGRADED:
        return CheckStatus.DEGRADED
    return None


def _flapping_transition_count(
    session: Session,
    check_id: int,
    now: datetime,
    incoming_event_type: Optional[str] = None,
) -> tuple[int, list[str], int]:
    window_seconds = max(60, int(os.environ.get("FLAP_WINDOW_SECONDS", "900")))
    cutoff = now - timedelta(seconds=window_seconds)
    with session.no_autoflush:
        rows = session.exec(
            select(Event)
            .where(
                Event.check_id == check_id,
                Event.created_at >= cutoff,
                Event.event_type.in_([EventType.DOWN, EventType.HTTP_FAILURE, EventType.UP, EventType.DEGRADED]),
            )
            .order_by(Event.created_at)
        ).all()

    states: list[str] = []
    for row in rows:
        state = _event_state(getattr(row, "event_type", None))
        if state:
            states.append(state)
    incoming_state = _event_state(incoming_event_type)
    if incoming_state:
        states.append(incoming_state)

    collapsed: list[str] = []
    for state in states:
        if not collapsed or collapsed[-1] != state:
            collapsed.append(state)
    transitions = max(0, len(collapsed) - 1)
    return transitions, collapsed, window_seconds


def _log_flapping_suppression(
    session: Session,
    project: Project,
    check: Check,
    now: datetime,
    *,
    event_type: str,
    reason: Optional[str],
    transitions: int,
    states: list[str],
    window_seconds: int,
) -> None:
    cutoff = now - timedelta(seconds=window_seconds)
    with session.no_autoflush:
        existing = session.exec(
            select(AuditLog).where(
                AuditLog.action == "flapping_alert_suppressed",
                AuditLog.target_type == "check",
                AuditLog.target_id == check.id,
                AuditLog.created_at >= cutoff,
            )
        ).first()
    if not existing:
        details = json.dumps(
            {
                "project_id": project.id,
                "check_id": check.id,
                "event_type": event_type,
                "reason": (reason or "")[:200],
                "transitions": transitions,
                "states": states[-10:],
                "window_seconds": window_seconds,
            }
        )
        session.add(
            AuditLog(
                actor="worker",
                action="flapping_alert_suppressed",
                target_type="check",
                target_id=check.id,
                details=details,
                actor_ip=None,
                user_agent=None,
            )
        )

    with session.no_autoflush:
        existing_anomaly = session.exec(
            select(Anomaly).where(
                Anomaly.check_id == check.id,
                Anomaly.type == "flapping",
                Anomaly.created_at >= cutoff,
            )
        ).first()
    if not existing_anomaly:
        session.add(
            Anomaly(
                check_id=check.id,
                incident_id=None,
                type="flapping",
                severity=float(transitions),
                window_start=cutoff,
                window_end=now,
                evidence_json=json.dumps(
                    {
                        "event_type": event_type,
                        "reason": (reason or "")[:200],
                        "transitions": transitions,
                        "states": states[-10:],
                        "window_seconds": window_seconds,
                    }
                ),
                created_at=now,
            )
        )
    session.flush()


def _should_suppress_alert_due_to_flapping(
    session: Session,
    project: Project,
    check: Check,
    now: datetime,
    event_type: str,
    reason: Optional[str] = None,
) -> bool:
    if os.environ.get("FLAP_SUPPRESSION_ENABLED", "1") != "1":
        return False
    if event_type not in (EventType.DOWN, EventType.HTTP_FAILURE, EventType.UP, EventType.DEGRADED):
        return False
    transition_threshold = max(1, int(os.environ.get("FLAP_TRANSITIONS_THRESHOLD", "4")))
    transitions, states, window_seconds = _flapping_transition_count(
        session,
        check.id,
        now,
        incoming_event_type=event_type,
    )
    if transitions < transition_threshold:
        return False
    try:
        _log_flapping_suppression(
            session,
            project,
            check,
            now,
            event_type=event_type,
            reason=reason,
            transitions=transitions,
            states=states,
            window_seconds=window_seconds,
        )
    except Exception:
        logger.exception("Failed to record flapping suppression for check %s", check.id)
    return True


def _in_maintenance(check: Check, project: Project, now: datetime) -> bool:
    """Return True if the given check is within a maintenance window.

    Maintenance window is optional and defined by `maintenance_starts_at`
    and `maintenance_ends_at` on the `Check` model. If both are set and
    `now` falls between them, alerts should be suppressed.
    """
    # check-level window
    start = getattr(check, "maintenance_starts_at", None)
    end = getattr(check, "maintenance_ends_at", None)
    if start is not None and end is not None and start <= now <= end:
        return True
    # project-level window
    pstart = getattr(project, "maintenance_starts_at", None)
    pend = getattr(project, "maintenance_ends_at", None)
    if pstart is not None and pend is not None and pstart <= now <= pend:
        return True
    return False


def _http_check(url: str, timeout: int, retries: int) -> Tuple[bool, str, Optional[float]]:
    """Perform a simple HTTP GET with retries.

    Returns (ok: bool, reason: str, latency_ms: Optional[float]).
    """
    last_exc = None
    for attempt in range(max(1, retries)):
        try:
            start = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "LastPing/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                if 200 <= code < 300:
                    latency_ms = (time.time() - start) * 1000.0
                    return True, f"status={code}", latency_ms
                else:
                    last_exc = f"status={code}"
        except urllib.error.HTTPError as he:
            # record HTTP errors (4xx/5xx) as the last exception reason
            last_exc = f"http_error={getattr(he, 'code', 'unknown')}"
        except Exception as e:
            last_exc = str(e)
        # small backoff between retries
        time.sleep(0.5)
    return False, last_exc or "unknown", None


def _tcp_check(host: str, port: int, timeout: int) -> Tuple[bool, str, Optional[float]]:
    try:
        start = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = (time.time() - start) * 1000.0
            return True, "tcp_ok", latency_ms
    except Exception as exc:
        return False, str(exc), None


def _dns_check(host: str, record_type: Optional[str] = None) -> Tuple[bool, str, Optional[float]]:
    try:
        family = 0
        if record_type:
            rt = record_type.upper()
            if rt == "A":
                family = socket.AF_INET
            elif rt == "AAAA":
                family = socket.AF_INET6
        start = time.time()
        socket.getaddrinfo(host, None, family=family)
        latency_ms = (time.time() - start) * 1000.0
        return True, "dns_ok", latency_ms
    except Exception as exc:
        return False, str(exc), None


def _truncate(text: str, limit: int = 500) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _parse_script_args(args_json: Optional[str]) -> list[str]:
    """Parse Check.script_args JSON (List[str]) safely."""
    if not args_json:
        return []
    if isinstance(args_json, list):
        return [str(x) for x in args_json]
    if not isinstance(args_json, str):
        return []
    try:
        parsed = json.loads(args_json)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x is not None]
    except Exception:
        return []
    return []


def _resolve_script_path(script_path: str) -> Optional[str]:
    """Resolve a relative script path within CUSTOM_CHECKS_DIR.

    We intentionally disallow absolute paths and path traversal so that
    "script checks" can only execute scripts the server operator has placed
    under a dedicated directory.
    """
    if not script_path or not isinstance(script_path, str):
        return None

    sp = script_path.strip()
    if not sp:
        return None
    # absolute paths or drive letters are not allowed
    if sp.startswith(("/", "\\")) or ":" in sp:
        return None

    parts = [p for p in sp.replace("\\", "/").split("/") if p]
    if any(p == ".." for p in parts):
        return None

    base_dir = os.environ.get("CUSTOM_CHECKS_DIR") or os.path.join(os.getcwd(), "custom_checks")
    base_real = os.path.realpath(base_dir)
    full_real = os.path.realpath(os.path.join(base_real, *parts))

    # ensure path stays under base dir
    base_prefix = base_real if base_real.endswith(os.sep) else (base_real + os.sep)
    if not (full_real == base_real or full_real.startswith(base_prefix)):
        return None
    if not os.path.isfile(full_real):
        return None
    return full_real


def _run_subprocess_sandboxed(cmd: list[str], timeout_s: int, env: dict) -> tuple[int, str, str, bool]:
    """Run a subprocess with basic safety controls.

    Returns (returncode, stdout, stderr, timed_out).
    """
    timeout_s = max(1, int(timeout_s or 1))

    # Best-effort resource limits on POSIX.
    preexec_fn = None
    if os.name == "posix":
        def _limit():
            try:
                import resource

                # CPU seconds hard limit (a little above timeout).
                resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 1, timeout_s + 1))
                # Max file size written by the process (bytes).
                resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))
                # Max processes (defense-in-depth; not supported everywhere).
                try:
                    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
                except Exception:
                    pass
                # Address space cap (bytes) to avoid runaway memory usage.
                try:
                    mem = int(os.environ.get("SCRIPT_CHECK_MAX_MEMORY_MB", "256")) * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
                except Exception:
                    pass
            except Exception:
                return

        preexec_fn = _limit

    # Put the process in its own group/session so we can kill children on timeout.
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": env,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
        popen_kwargs["preexec_fn"] = preexec_fn
    else:
        try:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        except Exception:
            pass

    proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode or 0, out or "", err or "", False
    except subprocess.TimeoutExpired:
        # Kill the whole process group if possible.
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            out, err = proc.communicate(timeout=2)
        except Exception:
            out, err = "", ""
        return 124, out or "", err or "", True


def _script_check(check: Check, project: Project, timeout: int, retries: int) -> Tuple[bool, str, Optional[float]]:
    """Execute a custom script check.

    Success is exit code 0. Failure is any non-zero exit code or timeout.
    """
    script_path = getattr(check, "script_path", None)
    resolved = _resolve_script_path(script_path or "")
    if not resolved:
        return False, f"script_not_found:{script_path}", None

    args = _parse_script_args(getattr(check, "script_args", None))
    # If the script is a python file, run it with the current interpreter for portability.
    cmd = [sys.executable, resolved] if resolved.endswith(".py") else [resolved]
    cmd.extend(args)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "LASTPING_PROJECT_ID": str(getattr(project, "id", "")),
        "LASTPING_CHECK_ID": str(getattr(check, "id", "")),
        "LASTPING_CHECK_NAME": str(getattr(check, "name", "")),
        "LASTPING_CHECK_TYPE": str(getattr(check, "type", "")),
    }

    last_reason = "unknown"
    for attempt in range(max(1, int(retries or 1))):
        start = time.time()
        rc, out, err, timed_out = _run_subprocess_sandboxed(cmd, timeout, env)
        latency_ms = (time.time() - start) * 1000.0

        out_t = _truncate((out or "").strip())
        err_t = _truncate((err or "").strip())

        if timed_out:
            last_reason = f"timeout>{timeout}s"
        elif rc == 0:
            msg = out_t or "ok"
            return True, msg, latency_ms
        else:
            # Prefer stderr for failure reason.
            detail = err_t or out_t or "error"
            last_reason = f"exit={rc} {detail}".strip()
        # brief backoff between attempts
        if attempt < max(1, int(retries or 1)) - 1:
            time.sleep(0.5)

    return False, last_reason, None


def _project_is_throttled(session: Session, project: Project, now: datetime) -> bool:
    pr_count = getattr(project, "alert_rate_limit_count", 0) or 0
    pr_window = getattr(project, "alert_rate_limit_window", 0) or 0
    if not pr_count or not pr_window:
        return False
    cutoff = now - timedelta(seconds=pr_window)
    recent_events = session.exec(select(Event).where(Event.project_id == project.id, Event.created_at > cutoff)).all()
    recent_bad = [e for e in recent_events if e.event_type in (EventType.DOWN, EventType.HTTP_FAILURE)]
    return len(recent_bad) >= pr_count


def _trigger_escalation(session: Session, project: Project, now: datetime, reason: str, check: Check | None = None):
    try:
        from .alerts import notify_escalation

        last_es = getattr(project, "last_escalated_at", None)
        window = getattr(project, "alert_rate_limit_window", 0) or 0
        if (last_es is None) or ((now - last_es).total_seconds() > window):
            ok = notify_escalation(project, reason, check=check)
            project.last_escalated_at = now
            session.add(project)
            session.commit()
            return ok
    except Exception:
        logger.exception("Error triggering escalation")
    return False


def _check_escalation_due(check: Check, open_inc: Optional[Incident], now: datetime) -> bool:
    if open_inc is None:
        return False
    after_min = getattr(check, "escalation_after_minutes", None)
    if not after_min:
        return False
    elapsed = (now - open_inc.started_at).total_seconds()
    if elapsed < (after_min * 60):
        return False
    cooldown = getattr(check, "escalation_cooldown_seconds", 0) or 0
    last = getattr(check, "last_escalated_at", None)
    if last and (now - last).total_seconds() < cooldown:
        return False
    return True


def _trigger_check_escalation(session: Session, project: Project, check: Check, open_inc: Optional[Incident], now: datetime, reason: str) -> bool:
    if not _check_escalation_due(check, open_inc, now):
        return False
    try:
        from .alerts import notify_escalation

        ok = notify_escalation(project, reason, check=check)
        check.last_escalated_at = now
        session.add(check)
        session.commit()
        return ok
    except Exception:
        logger.exception("Error triggering check escalation")
        return False


def _is_degraded(check: Check, latency_ms: Optional[float]) -> bool:
    thr = getattr(check, "latency_threshold_ms", None)
    if thr is None or latency_ms is None:
        return False
    return latency_ms > thr


def _normalize_reason(reason: Optional[str]) -> str:
    if not reason:
        return "unknown"
    text = reason.strip().lower()
    # strip URLs and IPs, normalize numbers
    text = re.sub(r"https?://\\S+", "url", text)
    text = re.sub(r"\\b\\d{1,3}(?:\\.\\d{1,3}){3}\\b", "ip", text)
    text = re.sub(r"\\d+", "#", text)
    text = re.sub(r"\\s+", " ", text)
    return text[:160] if text else "unknown"


def _bucket_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _event_weight(event_type: str) -> float:
    if event_type in (EventType.DOWN, EventType.HTTP_FAILURE):
        return 1.0
    if event_type == EventType.DEGRADED:
        return 0.5
    return 0.0


def _linear_forecast(values):
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


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _compute_monthly_rollups(session: Session, project: Project, month_start: datetime, month_end: datetime):
    return _compute_rollups_for_range(session, project, month_start, month_end)


def _quarter_start(dt: datetime) -> datetime:
    q = (dt.month - 1) // 3
    month = q * 3 + 1
    return dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _compute_rollups_for_range(session: Session, project: Project, start: datetime, end: datetime):
    checks = session.exec(select(Check).where(Check.project_id == project.id)).all()
    rollups = []
    for chk in checks:
        snaps = session.exec(
            select(UptimeSnapshot)
            .where(
                UptimeSnapshot.project_id == project.id,
                UptimeSnapshot.check_id == chk.id,
                UptimeSnapshot.window_end >= start,
                UptimeSnapshot.window_end <= end,
            )
            .order_by(UptimeSnapshot.window_end.desc())
        ).all()
        latest = {}
        for s in snaps:
            day = s.window_end.date().isoformat()
            if day not in latest:
                latest[day] = s
        vals = [s.uptime_percent for s in latest.values()]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        rollups.append({
            "check_id": chk.id,
            "uptime_percent": avg,
            "slo_met": (project.slo_target is not None and avg >= project.slo_target) if project.slo_target is not None else None,
            "sla_met": (project.sla_target is not None and avg >= project.sla_target) if project.sla_target is not None else None,
        })

    if rollups:
        agg = sum([r["uptime_percent"] for r in rollups]) / len(rollups)
        rollups.append({
            "check_id": None,
            "uptime_percent": agg,
            "slo_met": (project.slo_target is not None and agg >= project.slo_target) if project.slo_target is not None else None,
            "sla_met": (project.sla_target is not None and agg >= project.sla_target) if project.sla_target is not None else None,
        })
    return rollups


def _maybe_archive_monthly_rollups(session: Session, now: datetime, project_ids):
    global _LAST_ARCHIVE_RUN
    interval = int(os.environ.get("ARCHIVE_ROLLUP_INTERVAL_SECONDS", "86400"))
    if _LAST_ARCHIVE_RUN and (now - _LAST_ARCHIVE_RUN).total_seconds() < interval:
        return
    _LAST_ARCHIVE_RUN = now

    # archive previous full month only
    month_start = _month_start(now)
    prev_end = month_start
    prev_start = _month_start(month_start - timedelta(days=1))
    period = prev_start.strftime("%Y-%m")

    for pid in project_ids:
        project = session.get(Project, pid)
        if not project:
            continue
        rollups = _compute_monthly_rollups(session, project, prev_start, prev_end)
        if not rollups:
            continue
        for r in rollups:
            existing = session.exec(
                select(AvailabilityRollup).where(
                    AvailabilityRollup.project_id == pid,
                    AvailabilityRollup.check_id == r["check_id"],
                    AvailabilityRollup.period_type == "month",
                    AvailabilityRollup.period == period,
                )
            ).first()
            if existing:
                continue
            try:
                row = AvailabilityRollup(
                    project_id=pid,
                    check_id=r["check_id"],
                    period_type="month",
                    period=period,
                    period_start=prev_start,
                    period_end=prev_end,
                    uptime_percent=r["uptime_percent"],
                    slo_met=r["slo_met"],
                    sla_met=r["sla_met"],
                )
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to archive monthly rollup for project %s check %s", pid, r["check_id"])


def _maybe_archive_quarterly_rollups(session: Session, now: datetime, project_ids):
    if os.environ.get("ARCHIVE_QUARTERLY_ENABLED", "0") != "1":
        return
    interval = int(os.environ.get("ARCHIVE_QUARTERLY_INTERVAL_SECONDS", "604800"))
    global _LAST_QUARTERLY_RUN
    if _LAST_QUARTERLY_RUN and (now - _LAST_QUARTERLY_RUN).total_seconds() < interval:
        return
    _LAST_QUARTERLY_RUN = now

    # archive previous full quarter
    quarter_start = _quarter_start(now)
    prev_end = quarter_start
    prev_start = _quarter_start(prev_end - timedelta(days=1))
    qnum = (prev_start.month - 1) // 3 + 1
    period = f"{prev_start.year}-Q{qnum}"

    for pid in project_ids:
        project = session.get(Project, pid)
        if not project:
            continue
        rollups = _compute_rollups_for_range(session, project, prev_start, prev_end)
        if not rollups:
            continue
        for r in rollups:
            existing = session.exec(
                select(AvailabilityRollup).where(
                    AvailabilityRollup.project_id == pid,
                    AvailabilityRollup.check_id == r["check_id"],
                    AvailabilityRollup.period_type == "quarter",
                    AvailabilityRollup.period == period,
                )
            ).first()
            if existing:
                continue
            try:
                row = AvailabilityRollup(
                    project_id=pid,
                    check_id=r["check_id"],
                    period_type="quarter",
                    period=period,
                    period_start=prev_start,
                    period_end=prev_end,
                    uptime_percent=r["uptime_percent"],
                    slo_met=r["slo_met"],
                    sla_met=r["sla_met"],
                )
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to archive quarterly rollup for project %s check %s", pid, r["check_id"])


def _maybe_log_rollup_archive_health(session: Session, now: datetime, project_ids):
    if os.environ.get("ROLLUP_ARCHIVE_ALERTS_ENABLED", "1") != "1":
        return
    window_seconds = int(os.environ.get("ROLLUP_ARCHIVE_ALERT_WINDOW_SECONDS", "86400"))
    monthly_grace_days = int(os.environ.get("ROLLUP_ARCHIVE_GRACE_DAYS", "3"))
    quarterly_grace_days = int(os.environ.get("ROLLUP_QUARTERLY_GRACE_DAYS", "10"))
    alert_to = os.environ.get("ROLLUP_ARCHIVE_ALERT_EMAIL") or os.environ.get("ALERT_ESCALATION_EMAIL") or os.environ.get("ALERT_EMAIL_TO")

    month_start = _month_start(now)
    prev_month_end = month_start
    prev_month_start = _month_start(month_start - timedelta(days=1))
    month_period = prev_month_start.strftime("%Y-%m")
    month_due = now >= (month_start + timedelta(days=monthly_grace_days))

    quarter_start = _quarter_start(now)
    prev_quarter_end = quarter_start
    prev_quarter_start = _quarter_start(quarter_start - timedelta(days=1))
    qnum = (prev_quarter_start.month - 1) // 3 + 1
    quarter_period = f"{prev_quarter_start.year}-Q{qnum}"
    quarter_due = now >= (quarter_start + timedelta(days=quarterly_grace_days))

    for pid in project_ids:
        project = session.get(Project, pid)
        if not project:
            continue

        if month_due:
            exists = session.exec(
                select(AvailabilityRollup).where(
                    AvailabilityRollup.project_id == pid,
                    AvailabilityRollup.check_id == None,
                    AvailabilityRollup.period_type == "month",
                    AvailabilityRollup.period == month_period,
                )
            ).first()
            if not exists:
                cutoff = now - timedelta(seconds=window_seconds)
                recent = session.exec(
                    select(AuditLog).where(
                        AuditLog.action == "rollup_archive_missing",
                        AuditLog.target_type == "project",
                        AuditLog.target_id == pid,
                        AuditLog.created_at >= cutoff,
                    )
                ).first()
                if not recent:
                    details = json.dumps({"period_type": "month", "period": month_period})
                    al = AuditLog(actor="worker", action="rollup_archive_missing", target_type="project", target_id=pid, details=details, actor_ip=None, user_agent=None)
                    session.add(al)
                    session.commit()
                    if alert_to:
                        subj = f"[LastPing] Rollup archive missing: {project.name} ({month_period})"
                        body = f"Monthly rollup for {project.name} is missing for period {month_period}."
                        send_email(subj, body, to=alert_to)

        if quarter_due:
            exists = session.exec(
                select(AvailabilityRollup).where(
                    AvailabilityRollup.project_id == pid,
                    AvailabilityRollup.check_id == None,
                    AvailabilityRollup.period_type == "quarter",
                    AvailabilityRollup.period == quarter_period,
                )
            ).first()
            if not exists:
                cutoff = now - timedelta(seconds=window_seconds)
                recent = session.exec(
                    select(AuditLog).where(
                        AuditLog.action == "rollup_archive_missing",
                        AuditLog.target_type == "project",
                        AuditLog.target_id == pid,
                        AuditLog.created_at >= cutoff,
                    )
                ).first()
                if not recent:
                    details = json.dumps({"period_type": "quarter", "period": quarter_period})
                    al = AuditLog(actor="worker", action="rollup_archive_missing", target_type="project", target_id=pid, details=details, actor_ip=None, user_agent=None)
                    session.add(al)
                    session.commit()
                    if alert_to:
                        subj = f"[LastPing] Rollup archive missing: {project.name} ({quarter_period})"
                        body = f"Quarterly rollup for {project.name} is missing for period {quarter_period}."
                        send_email(subj, body, to=alert_to)


def _compute_early_warnings(session: Session, project_id: int, now: datetime, recent_hours: int, baseline_days: int, min_events: int, z_threshold: float, ratio_threshold: float):
    recent_start = now - timedelta(hours=recent_hours)
    baseline_start = now - timedelta(days=baseline_days)
    if baseline_start >= recent_start:
        return []
    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= baseline_start,
        Event.created_at <= now,
    )
    events = session.exec(stmt).all()
    baseline_counts = {}
    baseline_total = {}
    recent_total = {}
    recent_sig = {}
    for ev in events:
        if ev.event_type not in (EventType.DOWN, EventType.HTTP_FAILURE, EventType.DEGRADED):
            continue
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        if ev.created_at >= recent_start:
            recent_total[ev.check_id] = recent_total.get(ev.check_id, 0.0) + weight
            sig = _normalize_reason(getattr(ev, "message", None))
            recent_sig.setdefault(ev.check_id, {})[sig] = recent_sig.get(ev.check_id, {}).get(sig, 0) + 1
        else:
            bucket = _bucket_hour(ev.created_at)
            baseline_counts.setdefault(ev.check_id, {})[bucket] = baseline_counts.get(ev.check_id, {}).get(bucket, 0.0) + weight
            baseline_total[ev.check_id] = baseline_total.get(ev.check_id, 0.0) + weight

    baseline_hours = int((recent_start - baseline_start).total_seconds() / 3600) or 1
    warnings = []
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
        warnings.append({
            "check_id": check_id,
            "severity": severity,
            "signal": signal,
            "recent_count": recent_cnt,
            "recent_rate_per_hour": round(recent_rate, 4),
            "baseline_mean_per_hour": round(base_mean, 4),
            "baseline_std_per_hour": round(std, 4),
            "ratio": round(ratio, 4) if ratio is not None else None,
            "zscore": round(zscore, 4) if zscore is not None else None,
            "top_signature": top_sig,
        })
    return warnings


def _maybe_log_early_warnings(session: Session, now: datetime, project_ids):
    global _LAST_EARLY_WARNING_RUN
    if os.environ.get("EARLY_WARNING_ENABLED", "1") == "0":
        return
    interval = int(os.environ.get("EARLY_WARNING_INTERVAL_SECONDS", "600"))
    if _LAST_EARLY_WARNING_RUN and (now - _LAST_EARLY_WARNING_RUN).total_seconds() < interval:
        return
    _LAST_EARLY_WARNING_RUN = now
    recent_hours = int(os.environ.get("EARLY_WARNING_RECENT_HOURS", "3"))
    baseline_days = int(os.environ.get("EARLY_WARNING_BASELINE_DAYS", "14"))
    min_events = int(os.environ.get("EARLY_WARNING_MIN_EVENTS", "3"))
    z_threshold = float(os.environ.get("EARLY_WARNING_Z_THRESHOLD", "2.5"))
    ratio_threshold = float(os.environ.get("EARLY_WARNING_RATIO_THRESHOLD", "2.0"))
    log_window = int(os.environ.get("EARLY_WARNING_LOG_WINDOW_SECONDS", "3600"))

    for pid in project_ids:
        warnings = _compute_early_warnings(session, pid, now, recent_hours, baseline_days, min_events, z_threshold, ratio_threshold)
        for warn in warnings:
            check_id = warn.get("check_id")
            cutoff = now - timedelta(seconds=log_window)
            existing = session.exec(
                select(AuditLog).where(
                    AuditLog.action == "early_warning",
                    AuditLog.target_type == "check",
                    AuditLog.target_id == check_id,
                    AuditLog.created_at >= cutoff,
                )
            ).first()
            if existing:
                continue
            try:
                al = AuditLog(
                    actor="worker",
                    action="early_warning",
                    target_type="check",
                    target_id=check_id,
                    details=json.dumps(warn),
                    actor_ip=None,
                    user_agent=None,
                )
                session.add(al)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to log early warning for check %s", check_id)


def _compute_predictive_warnings(session: Session, project_id: int, now: datetime, recent_hours: int, min_events: int, ratio_threshold: float):
    start = now - timedelta(hours=recent_hours)
    stmt = select(Event).where(
        Event.project_id == project_id,
        Event.created_at >= start,
        Event.created_at <= now,
    )
    events = session.exec(stmt).all()
    buckets = {}
    for ev in events:
        weight = _event_weight(ev.event_type)
        if weight <= 0:
            continue
        hour = ev.created_at.replace(minute=0, second=0, microsecond=0).isoformat()
        check_bucket = buckets.setdefault(ev.check_id, {})
        check_bucket[hour] = check_bucket.get(hour, 0.0) + weight

    warnings = []
    for check_id, counts in buckets.items():
        series = []
        for i in range(recent_hours):
            h = (start + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0).isoformat()
            series.append(counts.get(h, 0.0))
        if sum(series) < min_events:
            continue
        slope, _intercept, next_val = _linear_forecast(series)
        base_mean = sum(series) / len(series)
        ratio = (next_val / base_mean) if base_mean > 0 else None
        if next_val >= min_events and ratio is not None and ratio >= ratio_threshold and slope > 0:
            warnings.append({
                "check_id": check_id,
                "recent_hours": recent_hours,
                "recent_total": round(sum(series), 4),
                "baseline_mean_per_hour": round(base_mean, 4),
                "trend_slope_per_hour": round(slope, 4),
                "predicted_next_hour": round(next_val, 4),
                "ratio": round(ratio, 4),
            })
    return warnings


def _maybe_log_predictive_warnings(session: Session, now: datetime, project_ids):
    global _LAST_PREDICTIVE_RUN
    interval = int(os.environ.get("PREDICTIVE_RUN_INTERVAL_SECONDS", "900"))
    if _LAST_PREDICTIVE_RUN and (now - _LAST_PREDICTIVE_RUN).total_seconds() < interval:
        return
    _LAST_PREDICTIVE_RUN = now

    recent_hours = int(os.environ.get("PREDICTIVE_RECENT_HOURS", "24"))
    min_events = int(os.environ.get("PREDICTIVE_MIN_EVENTS", "3"))
    ratio_threshold = float(os.environ.get("PREDICTIVE_RATIO_THRESHOLD", "2.0"))
    ml_z_threshold = float(os.environ.get("PREDICTIVE_ML_Z_THRESHOLD", "2.0"))
    log_window = int(os.environ.get("PREDICTIVE_LOG_WINDOW_SECONDS", "3600"))

    for pid in project_ids:
        # ML-backed predictive warnings (if trained models exist)
        try:
            from .predictive_models import predictive_warnings_from_models
            ml_warnings = predictive_warnings_from_models(
                session=session,
                project_id=pid,
                now=now,
                recent_hours=recent_hours,
                min_events=min_events,
                z_threshold=ml_z_threshold,
            )
            for warn in ml_warnings:
                check_id = warn.get("check_id")
                cutoff = now - timedelta(seconds=log_window)
                existing = session.exec(
                    select(AuditLog).where(
                        AuditLog.action == "predictive_ml_warning",
                        AuditLog.target_type == "check",
                        AuditLog.target_id == check_id,
                        AuditLog.created_at >= cutoff,
                    )
                ).first()
                if existing:
                    continue
                al = AuditLog(
                    actor="worker",
                    action="predictive_ml_warning",
                    target_type="check",
                    target_id=check_id,
                    details=json.dumps(warn),
                    actor_ip=None,
                    user_agent=None,
                )
                session.add(al)
                session.commit()
        except Exception:
            logger.exception("Failed to compute ML predictive warnings")

        warnings = _compute_predictive_warnings(session, pid, now, recent_hours, min_events, ratio_threshold)
        for warn in warnings:
            check_id = warn.get("check_id")
            cutoff = now - timedelta(seconds=log_window)
            existing = session.exec(
                select(AuditLog).where(
                    AuditLog.action == "predictive_warning",
                    AuditLog.target_type == "check",
                    AuditLog.target_id == check_id,
                    AuditLog.created_at >= cutoff,
                )
            ).first()
            if existing:
                continue
            try:
                al = AuditLog(
                    actor="worker",
                    action="predictive_warning",
                    target_type="check",
                    target_id=check_id,
                    details=json.dumps(warn),
                    actor_ip=None,
                    user_agent=None,
                )
                session.add(al)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to log predictive warning for check %s", check_id)


def _compute_uptime_snapshots(session: Session, now: datetime):
    """Compute and persist a short-term uptime/MTTR snapshot (last 24h) for each check."""
    window_end = now
    window_start = now - timedelta(hours=24)
    stmt = select(Check)
    all_checks = session.exec(stmt).all()
    for c in all_checks:
        ev_stmt = select(Event).where(
            Event.project_id == c.project_id,
            Event.check_id == c.id,
            Event.created_at >= window_start,
            Event.created_at <= window_end,
        ).order_by(Event.created_at)
        events = session.exec(ev_stmt).all()
        prev_stmt = select(Event).where(
            Event.project_id == c.project_id,
            Event.check_id == c.id,
            Event.created_at < window_start,
        ).order_by(Event.created_at.desc())
        prev = session.exec(prev_stmt).first()
        current_state = "up"
        if prev and prev.event_type in ("down", "http_failure"):
            current_state = "down"
        downtime = 0.0
        last_change = window_start
        for ev in events:
            if ev.event_type in ("down", "http_failure") and current_state == "up":
                current_state = "down"
                last_change = ev.created_at
            elif ev.event_type == "up" and current_state == "down":
                downtime += (ev.created_at - last_change).total_seconds()
                current_state = "up"
                last_change = ev.created_at
        if current_state == "down":
            downtime += (window_end - last_change).total_seconds()
        total = (window_end - window_start).total_seconds()
        uptime_pct = max(0.0, (total - downtime) / total * 100.0) if total > 0 else 100.0

        downs = []
        for i, ev in enumerate(events):
            if ev.event_type in ("down", "http_failure"):
                for j in range(i + 1, len(events)):
                    if events[j].event_type == "up":
                        dur = (events[j].created_at - ev.created_at).total_seconds()
                        downs.append(dur)
                        break
        mttr = None
        if downs:
            mttr = sum(downs) / len(downs)

        snap = UptimeSnapshot(
            project_id=c.project_id,
            check_id=c.id,
            window_start=window_start,
            window_end=window_end,
            uptime_percent=uptime_pct,
            mttr_seconds=mttr,
        )
        try:
            session.add(snap)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Failed to persist uptime snapshot for check %s", c.id)


def _log_similar_incidents(session: Session, project: Project, incident_id: int, reason: Optional[str]):
    threshold = float(os.environ.get("INCIDENT_SIMILARITY_THRESHOLD", "0.35"))
    if threshold <= 0.0:
        return
    days = int(os.environ.get("INCIDENT_SIMILARITY_DAYS", "90"))
    limit = int(os.environ.get("INCIDENT_SIMILARITY_LIMIT", "3"))
    matches = find_similar_incidents(
        session=session,
        project_id=project.id,
        target_text=reason,
        days=days,
        limit=limit,
        threshold=threshold,
        target_incident_id=incident_id,
    )
    if not matches:
        return
    try:
        al = AuditLog(
            actor="worker",
            action="similar_incident_ml",
            target_type="incident",
            target_id=incident_id,
            details=json.dumps({"matches": matches}),
            actor_ip=None,
            user_agent=None,
        )
        session.add(al)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to log similar incident for %s", incident_id)


def _incident_signature(session: Session, incident_id: int) -> str:
    ev = session.exec(select(Event).where(Event.incident_id == incident_id).order_by(Event.created_at)).first()
    return _normalize_reason(getattr(ev, "message", None) if ev else None)


def _find_group_root(session: Session, project_id: int, signature: str, now: datetime) -> Optional[int]:
    if not signature:
        return None
    cutoff = now - timedelta(seconds=GROUP_WINDOW)
    candidates = session.exec(
        select(Incident)
        .where(
            Incident.project_id == project_id,
            Incident.resolved_at == None,
            Incident.started_at >= cutoff,
        )
        .order_by(Incident.started_at.desc())
    ).all()
    for cand in candidates:
        cand_sig = _incident_signature(session, cand.id)
        if cand_sig == signature:
            return cand.group_id or cand.id
    return None


def _worker_id() -> str:
    return os.environ.get("WORKER_ID") or os.environ.get("WORKER_REGION") or socket.gethostname()


def _region_allows(worker_region: Optional[str], check_region: Optional[str]) -> bool:
    """Return True if the worker should process a check for the given region setting.

    - check_region may be a single value or a comma/space-separated list.
    - "*" or "all" means any worker region.
    - If worker_region is unset, process all checks for dev/local runs.
    """
    if not check_region:
        return True
    raw = str(check_region).strip().lower()
    if raw in ("*", "all", "any"):
        return True
    if not worker_region:
        return True
    allowed = [r for r in re.split(r"[,\s]+", raw) if r]
    return worker_region.strip().lower() in allowed


def _allow_region_or_failover(session: Session, worker_region: Optional[str], check: Check, now: datetime) -> bool:
    if _region_allows(worker_region, getattr(check, "region", None)):
        return True
    # failover: allow other regions to pick up checks after leases expire
    if os.environ.get("WORKER_REGION_FAILOVER", "0") != "1":
        return False
    grace = int(os.environ.get("WORKER_FAILOVER_AFTER_SECONDS", "300"))
    skew_tolerance = max(0, int(os.environ.get("WORKER_CLOCK_SKEW_TOLERANCE_SECONDS", "5")))
    db_now = _as_utc_naive(_db_now(session))
    lease = session.get(CheckLease, check.id)
    if lease is None or lease.lease_expires_at is None:
        # Do not allow non-matching regions to claim a check before its
        # owning region has acquired at least one lease.
        return False
    lease_expires = _as_utc_naive(lease.lease_expires_at)
    return lease_expires <= (db_now - timedelta(seconds=(grace + skew_tolerance)))


def _acquire_lease(session: Session, check: Check, now: datetime) -> bool:
    if os.environ.get("WORKER_LEASES", "1") == "0":
        return True
    lease_seconds = int(os.environ.get("WORKER_LEASE_SECONDS", "120"))
    skew_tolerance = max(0, int(os.environ.get("WORKER_CLOCK_SKEW_TOLERANCE_SECONDS", "5")))
    db_now = _db_now(session)
    owner = _worker_id()
    expires_at = db_now + timedelta(seconds=lease_seconds)
    expiration_cutoff = db_now - timedelta(seconds=skew_tolerance)
    try:
        # Atomic lease update: only succeeds if lease is expired or already owned by this worker.
        update_stmt = (
            update(CheckLease)
            .where(
                CheckLease.check_id == check.id,
                or_(
                    CheckLease.lease_expires_at.is_(None),
                    CheckLease.lease_expires_at <= expiration_cutoff,
                    CheckLease.lease_owner == owner,
                ),
            )
            .values(
                lease_owner=owner,
                lease_expires_at=expires_at,
                updated_at=now,
                lease_fence=CheckLease.lease_fence + 1,
            )
        )
        result = session.exec(update_stmt)
        if (result.rowcount or 0) > 0:
            session.commit()
            return True

        # No updatable row found. Try initial insert for first-time acquisition.
        existing = session.get(CheckLease, check.id)
        if existing is None:
            session.add(
                CheckLease(
                    check_id=check.id,
                    lease_owner=owner,
                    lease_expires_at=expires_at,
                    lease_fence=1,
                    updated_at=now,
                )
            )
            session.commit()
            return True
        return False
    except IntegrityError:
        # Another worker inserted concurrently. Retry the atomic update once.
        session.rollback()
        try:
            retry_stmt = (
                update(CheckLease)
                .where(
                    CheckLease.check_id == check.id,
                    or_(
                        CheckLease.lease_expires_at.is_(None),
                        CheckLease.lease_expires_at <= expiration_cutoff,
                        CheckLease.lease_owner == owner,
                    ),
                )
                .values(
                    lease_owner=owner,
                    lease_expires_at=expires_at,
                    updated_at=now,
                    lease_fence=CheckLease.lease_fence + 1,
                )
            )
            retry_result = session.exec(retry_stmt)
            if (retry_result.rowcount or 0) > 0:
                session.commit()
                return True
            return False
        except Exception:
            session.rollback()
            logger.exception("Failed to acquire lease for check %s after retry", getattr(check, "id", None))
            return False
    except Exception:
        session.rollback()
        logger.exception("Failed to acquire lease for check %s", getattr(check, "id", None))
        return False


def _remediation_skip_reason(session: Session, hook: RemediationHook, project: Project, check: Check, now: datetime) -> Optional[str]:
    if hook.disabled_at is not None or not getattr(hook, "enabled", True):
        return "disabled"
    if hook.check_id and hook.check_id != check.id:
        return "check_mismatch"
    if getattr(hook, "require_secret", False) and not hook.secret:
        try:
            al = AuditLog(actor="worker", action="skip_remediation", target_type="remediation_hook", target_id=hook.id, details="missing_secret", actor_ip=None, user_agent=None)
            session.add(al)
            session.commit()
        except Exception:
            pass
        return "missing_secret"
    if not getattr(hook, "allow_during_maintenance", False) and _in_maintenance(check, project, now):
        try:
            al = AuditLog(actor="worker", action="skip_remediation", target_type="remediation_hook", target_id=hook.id, details="maintenance_window", actor_ip=None, user_agent=None)
            session.add(al)
            session.commit()
        except Exception:
            pass
        return "maintenance_window"
    if hook.last_triggered_at and (now - hook.last_triggered_at).total_seconds() < (hook.cooldown_seconds or 0):
        return "cooldown"
    max_per_day = getattr(hook, "max_triggers_per_day", None)
    if max_per_day:
        day_start = now - timedelta(days=1)
        recent = session.exec(select(RemediationLog).where(RemediationLog.hook_id == hook.id, RemediationLog.created_at >= day_start)).all()
        if len(recent) >= max_per_day:
            try:
                al = AuditLog(actor="worker", action="skip_remediation", target_type="remediation_hook", target_id=hook.id, details="max_per_day", actor_ip=None, user_agent=None)
                session.add(al)
                session.commit()
            except Exception:
                pass
            return "max_per_day"
    return None


def _queue_remediation_approval(session: Session, hook: RemediationHook, project: Project, check: Check, event_type: str, reason: Optional[str], now: datetime) -> bool:
    existing = session.exec(
        select(RemediationApproval).where(
            RemediationApproval.hook_id == hook.id,
            RemediationApproval.check_id == check.id,
            RemediationApproval.event_type == event_type,
            RemediationApproval.status == "pending",
        )
    ).first()
    if existing:
        return False
    ttl_min = int(os.environ.get("REMEDIATION_APPROVAL_TTL_MINUTES", "60"))
    expires_at = now + timedelta(minutes=ttl_min) if ttl_min > 0 else None
    approval = RemediationApproval(
        hook_id=hook.id,
        project_id=project.id,
        check_id=check.id,
        event_type=event_type,
        reason=reason,
        status="pending",
        requested_at=now,
        expires_at=expires_at,
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    try:
        al = AuditLog(actor="worker", action="request_remediation_approval", target_type="remediation_approval", target_id=approval.id, details=f"hook_id={hook.id},check_id={check.id}", actor_ip=None, user_agent=None)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return True


def _execute_remediation_hook(session: Session, project: Project, check: Check, hook: RemediationHook, event_type: str, reason: Optional[str], now: datetime, approval_id: Optional[int] = None) -> Tuple[str, Optional[int], Optional[str]]:
    payload = {
        "project_id": project.id,
        "project": project.name,
        "check_id": check.id,
        "check": check.name,
        "event": event_type,
        "reason": reason,
        "timestamp": now.isoformat(),
    }
    status = "error"
    code = None
    msg = None
    try:
        method = (hook.method or "POST").upper()
        data = json.dumps(payload).encode("utf-8") if method in ("POST", "PUT", "PATCH") else None
        req = urllib.request.Request(hook.url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if hook.secret:
            req.add_header("X-REMEDIATION-SECRET", hook.secret)
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
        status = "ok" if code and 200 <= code < 300 else "error"
    except Exception as exc:
        msg = str(exc)
    hook.last_triggered_at = now
    if status != "ok":
        hook.failure_count = (hook.failure_count or 0) + 1
        threshold = getattr(hook, "disable_on_failure_count", None)
        if threshold and hook.failure_count >= threshold:
            hook.enabled = False
            hook.disabled_at = now
            hook.disabled_reason = f"failure_count>={threshold}"
            try:
                al = AuditLog(actor="worker", action="disable_remediation", target_type="remediation_hook", target_id=hook.id, details=hook.disabled_reason, actor_ip=None, user_agent=None)
                session.add(al)
            except Exception:
                pass
    else:
        hook.failure_count = 0
    session.add(hook)
    log = RemediationLog(
        hook_id=hook.id,
        project_id=project.id,
        check_id=check.id,
        event_type=event_type,
        status=status,
        response_code=code,
        message=msg,
    )
    session.add(log)
    try:
        detail = f"check_id={check.id}"
        if approval_id:
            detail = f"{detail},approval_id={approval_id}"
        al = AuditLog(actor="worker", action="trigger_remediation", target_type="remediation_hook", target_id=hook.id, details=detail, actor_ip=None, user_agent=None)
        session.add(al)
    except Exception:
        pass
    session.commit()
    return status, code, msg


def _process_remediation_approvals(session: Session, now: datetime):
    # expire pending approvals
    pending = session.exec(select(RemediationApproval).where(RemediationApproval.status == "pending", RemediationApproval.expires_at != None)).all()
    for ap in pending:
        if ap.expires_at and ap.expires_at <= now:
            ap.status = "expired"
            ap.decided_at = now
            ap.execution_message = "expired"
            session.add(ap)
    if pending:
        session.commit()

    approvals = session.exec(select(RemediationApproval).where(RemediationApproval.status == "approved", RemediationApproval.executed_at == None)).all()
    for ap in approvals:
        hook = session.get(RemediationHook, ap.hook_id)
        project = session.get(Project, ap.project_id)
        check = session.get(Check, ap.check_id)
        if not hook or not project or not check:
            ap.status = "failed"
            ap.execution_status = "missing_reference"
            ap.execution_message = "missing hook/project/check"
            ap.executed_at = now
            session.add(ap)
            session.commit()
            continue
        skip_reason = _remediation_skip_reason(session, hook, project, check, now)
        if skip_reason in ("cooldown", "maintenance_window", "max_per_day"):
            ap.execution_message = skip_reason
            session.add(ap)
            session.commit()
            continue
        if skip_reason:
            ap.status = "failed"
            ap.execution_status = skip_reason
            ap.execution_message = skip_reason
            ap.executed_at = now
            session.add(ap)
            session.commit()
            continue
        status, code, msg = _execute_remediation_hook(session, project, check, hook, ap.event_type, ap.reason, now, approval_id=ap.id)
        ap.executed_at = now
        ap.execution_status = status
        ap.execution_message = msg
        ap.status = "executed" if status == "ok" else "failed"
        session.add(ap)
        session.commit()


def _trigger_remediation(session: Session, project: Project, check: Check, event_type: str, reason: Optional[str], now: datetime):
    try:
        hooks = session.exec(
            select(RemediationHook).where(
                RemediationHook.project_id == project.id,
                RemediationHook.enabled == True,
                RemediationHook.event_type == event_type,
            )
        ).all()
        for hook in hooks:
            skip_reason = _remediation_skip_reason(session, hook, project, check, now)
            if skip_reason:
                continue
            if getattr(hook, "require_approval", False):
                _queue_remediation_approval(session, hook, project, check, event_type, reason, now)
                continue
            _execute_remediation_hook(session, project, check, hook, event_type, reason, now)
    except Exception:
        logger.exception("Error triggering remediation hooks for check %s", getattr(check, "id", None))


def _current_rotation_member(session: Session, rotation: OnCallRotation, now: datetime) -> Optional[OnCallMember]:
    members = session.exec(
        select(OnCallMember).where(OnCallMember.rotation_id == rotation.id, OnCallMember.active == True).order_by(OnCallMember.order)
    ).all()
    if not members:
        return None
    interval = max(1, rotation.interval_minutes or 1)
    elapsed = (now - rotation.start_at).total_seconds()
    idx = int(elapsed // (interval * 60)) % len(members)
    return members[idx]


def _oncall_enabled_for_check(project: Project, check: Check) -> bool:
    override = getattr(check, "alert_oncall_enabled", None)
    if override is not None:
        return bool(override)
    return bool(getattr(project, "oncall_enabled", False))


def _event_type_matches(esc: OnCallEscalation, event_type: str) -> bool:
    raw = getattr(esc, "event_types", None)
    if not raw:
        return True
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return event_type.lower() in parts


def _load_oncall_escalations(session: Session, project: Project, check: Check, event_type: str):
    def _fetch(cid):
        return session.exec(
            select(OnCallEscalation)
            .where(
                OnCallEscalation.project_id == project.id,
                OnCallEscalation.enabled == True,
                OnCallEscalation.check_id == cid,
            )
            .order_by(OnCallEscalation.level)
        ).all()

    escs = _fetch(check.id)
    escs = [e for e in escs if _event_type_matches(e, event_type)]
    if not escs:
        escs = _fetch(None)
        escs = [e for e in escs if _event_type_matches(e, event_type)]
    return escs


def _group_escalations_by_level(escalations):
    steps = {}
    for esc in escalations:
        lvl = esc.level or 0
        steps.setdefault(lvl, []).append(esc)
    ordered = []
    for lvl in sorted(steps.keys()):
        ordered.append((lvl, steps[lvl]))
    return ordered


def _send_oncall_target(session: Session, project: Project, check: Check, alert: OnCallAlert, esc: OnCallEscalation, now: datetime) -> bool:
    msg = f"[LastPing] {alert.event_type.upper()}: {project.name}/{check.name} {alert.message or ''}".strip()
    try:
        if esc.target_type == "rotation":
            if not esc.rotation_id:
                return False
            rotation = session.get(OnCallRotation, esc.rotation_id)
            if not rotation or not rotation.enabled:
                return False
            member = _current_rotation_member(session, rotation, now)
            if not member:
                return False
            ok = False
            if member.email:
                ok = send_email(f"[LastPing] {project.name} alert", msg, to=member.email) or ok
            if member.phone:
                ok = send_sms(msg, to=member.phone, project=project) or ok
            return ok
        if esc.target_type == "email" and esc.target_value:
            return send_email(f"[LastPing] {project.name} alert", msg, to=esc.target_value)
        if esc.target_type == "sms" and esc.target_value:
            return send_sms(msg, to=esc.target_value, project=project)
    except Exception:
        logger.exception("Error sending on-call notification")
    return False


def _ensure_oncall_alert(session: Session, project: Project, check: Check, event_type: str, message: Optional[str], now: datetime):
    if not _oncall_enabled_for_check(project, check):
        return
    if _should_suppress_alert_due_to_flapping(session, project, check, now, event_type, reason=message):
        return
    escs = _load_oncall_escalations(session, project, check, event_type)
    if not escs:
        return
    existing = session.exec(select(OnCallAlert).where(OnCallAlert.project_id == project.id, OnCallAlert.check_id == check.id, OnCallAlert.status == "open")).first()
    if existing:
        return
    alert = OnCallAlert(
        project_id=project.id,
        check_id=check.id,
        event_type=event_type,
        message=message,
        status="open",
        created_at=now,
        escalation_level=0,
        next_escalation_at=now,
    )
    session.add(alert)
    session.commit()


def _close_oncall_alerts(session: Session, check_id: int):
    alerts = session.exec(select(OnCallAlert).where(OnCallAlert.check_id == check_id, OnCallAlert.status == "open")).all()
    for alert in alerts:
        alert.status = "closed"
        session.add(alert)
    session.commit()


def _process_oncall_alerts(session: Session, now: datetime):
    alerts = session.exec(select(OnCallAlert).where(OnCallAlert.status == "open", OnCallAlert.next_escalation_at <= now)).all()
    for alert in alerts:
        project = session.get(Project, alert.project_id)
        check = session.get(Check, alert.check_id)
        if not project or not check:
            alert.status = "closed"
            session.add(alert)
            session.commit()
            continue
        if not _oncall_enabled_for_check(project, check):
            alert.status = "closed"
            session.add(alert)
            session.commit()
            continue
        if _in_maintenance(check, project, now):
            alert.next_escalation_at = now + timedelta(minutes=5)
            session.add(alert)
            session.commit()
            continue
        escs = _load_oncall_escalations(session, project, check, alert.event_type)
        steps = _group_escalations_by_level(escs)
        if not steps or alert.escalation_level >= len(steps):
            alert.status = "closed"
            session.add(alert)
            session.commit()
            continue
        _, step_escalations = steps[alert.escalation_level]
        step_ok = False
        max_delay = 0
        for esc in step_escalations:
            ok = _send_oncall_target(session, project, check, alert, esc, now)
            step_ok = step_ok or ok
            max_delay = max(max_delay, esc.delay_minutes or 0)
            if ok:
                try:
                    al = AuditLog(actor="worker", action="oncall_notify", target_type="oncall_escalation", target_id=esc.id, details=f"alert_id={alert.id}", actor_ip=None, user_agent=None)
                    session.add(al)
                except Exception:
                    pass
        if step_ok:
            alert.last_notified_at = now
            alert.escalation_level = alert.escalation_level + 1
            alert.next_escalation_at = now + timedelta(minutes=max(max_delay, 1))
        else:
            alert.next_escalation_at = now + timedelta(minutes=5)
        session.add(alert)
        session.commit()


def scan_checks_once(session: Session):
    # Load all checks and process each synchronously. The worker is
    # intentionally simple (single-threaded) to avoid race conditions
    # with the DB and to make behaviour easy to reason about in tests.
    stmt = select(Check)
    results = session.exec(stmt).all()
    now = _now()
    processed_oncall = False
    processed_remediation = False
    project_ids = set()
    worker_region = os.environ.get("WORKER_REGION")
    for check in results:
        project_ids.add(check.project_id)
        if not _allow_region_or_failover(session, worker_region, check, now):
            continue
        project = session.get(Project, check.project_id)
        if not project:
            continue
        if not _acquire_lease(session, check, now):
            continue
        maintenance = _in_maintenance(check, project, now)
        run_key = _check_run_key(check, now)

        # HEARTBEAT checks are driven by client pings; worker only
        # marks them DOWN when the expected interval + grace window
        # has passed since `last_ping`.
        if check.type == CheckType.HEARTBEAT:
            expected = check.expected_interval or 600
            grace = check.grace_period or 600
            last_ping = check.last_ping or getattr(check, "created_at", None)
            if last_ping is None:
                continue
            threshold = last_ping + timedelta(seconds=(expected + grace))

            if now > threshold:
                # Overdue heartbeat
                if check.status != CheckStatus.DOWN:
                    check.status = CheckStatus.DOWN
                    check.consecutive_failures = (check.consecutive_failures or 0) + 1
                    reason = "missed heartbeat"
                    signature = _normalize_reason(reason)
                    # find or create open incident for this check
                    created_incident = False
                    open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                    if not open_inc:
                        group_root = _find_group_root(session, check.project_id, signature, now)
                        open_inc = Incident(project_id=check.project_id, check_id=check.id, started_at=now, status="open", group_id=group_root)
                        session.add(open_inc)
                        session.commit()
                        session.refresh(open_inc)
                        created_incident = True
                    if created_incident:
                        _log_similar_incidents(session, project, open_inc.id, reason)
                    event_message = reason
                    if maintenance:
                        event_message = f"{reason} (suppressed due to maintenance)"
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DOWN, message=event_message, incident_id=open_inc.id)
                    session.add(event)
                    _record_check_result(
                        session,
                        check,
                        run_key=run_key,
                        status=CheckStatus.DOWN,
                        latency_ms=None,
                        error_message=reason,
                        incident_id=open_inc.id,
                        created_at=now,
                    )
                    if not maintenance:
                        _trigger_remediation(session, project, check, EventType.DOWN, reason, now)
                        _ensure_oncall_alert(session, project, check, EventType.DOWN, reason, now)
                    # alerting: only send if enabled and threshold reached and cooldown passed
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    flapping_suppressed = False
                    if should_alert and not maintenance:
                        flapping_suppressed = _should_suppress_alert_due_to_flapping(
                            session,
                            project,
                            check,
                            now,
                            EventType.DOWN,
                            reason=reason,
                        )
                    if should_alert:
                        if maintenance or flapping_suppressed:
                            session.add(check)
                            session.commit()
                            continue
                        # project-level throttling/escalation
                        throttled = _project_is_throttled(session, project, now)
                        if throttled:
                            _trigger_escalation(session, project, now, reason, check=check)
                            session.add(check)
                            session.commit()
                        else:
                            last_alert = check.last_alerted_at
                            cooldown = check.alert_cooldown or 0
                            if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
                                session.add(check)
                                session.commit()
                                try:
                                    ok = notify_down(check, project, reason="missed heartbeat")
                                    check.last_alerted_at = now
                                    check.last_alert_type = EventType.DOWN
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending DOWN alert")
                            else:
                                session.add(check)
                                session.commit()
                        _trigger_check_escalation(session, project, check, open_inc, now, reason)
                    else:
                        session.add(check)
                        session.commit()
                else:
                    check.consecutive_failures = (check.consecutive_failures or 0) + 1
                    open_inc = session.exec(
                        select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)
                    ).first()
                    _record_check_result(
                        session,
                        check,
                        run_key=run_key,
                        status=CheckStatus.DOWN,
                        latency_ms=None,
                        error_message="missed heartbeat",
                        incident_id=(open_inc.id if open_inc else None),
                        created_at=now,
                    )
                    # still down: allow re-alerts after cooldown if threshold met
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    flapping_suppressed = False
                    if should_alert and not maintenance:
                        flapping_suppressed = _should_suppress_alert_due_to_flapping(
                            session,
                            project,
                            check,
                            now,
                            EventType.DOWN,
                            reason="still down (missed heartbeat)",
                        )
                    if should_alert:
                        if maintenance or flapping_suppressed:
                            session.add(check)
                            session.commit()
                            continue
                        throttled = _project_is_throttled(session, project, now)
                        if throttled:
                            _trigger_escalation(session, project, now, "still down (missed heartbeat)", check=check)
                            session.add(check)
                            session.commit()
                        else:
                            last_alert = check.last_alerted_at
                            cooldown = check.alert_cooldown or 0
                            if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
                                session.add(check)
                                session.commit()
                                try:
                                    notify_down(check, project, reason="still down (missed heartbeat)")
                                    check.last_alerted_at = now
                                    check.last_alert_type = EventType.DOWN
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending repeated DOWN alert")
                            else:
                                session.add(check)
                                session.commit()
                        _trigger_check_escalation(session, project, check, open_inc, now, "still down (missed heartbeat)")
                    else:
                        session.add(check)
                        session.commit()
            else:
                if check.status == CheckStatus.DOWN:
                    check.status = CheckStatus.UP
                    check.consecutive_failures = 0
                    # close open incident if present
                    open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                    if open_inc:
                        open_inc.resolved_at = now
                        open_inc.status = "resolved"
                        session.add(open_inc)
                        session.commit()
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.UP, message="recovered", incident_id=(open_inc.id if open_inc else None))
                    session.add(event)
                    _record_check_result(
                        session,
                        check,
                        run_key=run_key,
                        status=CheckStatus.UP,
                        latency_ms=None,
                        error_message=None,
                        incident_id=(open_inc.id if open_inc else None),
                        created_at=now,
                    )
                    _close_oncall_alerts(session, check.id)
                    # recovery alert: respect cooldown and enabled
                    if check.alert_enabled and not maintenance:
                        flapping_suppressed = _should_suppress_alert_due_to_flapping(
                            session,
                            project,
                            check,
                            now,
                            EventType.UP,
                            reason="recovered",
                        )
                        last_alert = check.last_alerted_at
                        cooldown = check.alert_cooldown or 0
                        if (not flapping_suppressed) and ((last_alert is None) or ((now - last_alert).total_seconds() > cooldown)):
                            session.add(check)
                            session.commit()
                            try:
                                notify_recovery(check, project)
                                check.last_alerted_at = now
                                check.last_alert_type = EventType.UP
                                session.add(check)
                                session.commit()
                            except Exception:
                                logger.exception("Error sending recovery alert")
                        else:
                            session.add(check)
                            session.commit()
                    else:
                        session.add(check)
                        session.commit()
                else:
                    _record_check_result(
                        session,
                        check,
                        run_key=run_key,
                        status=check.status,
                        latency_ms=None,
                        error_message=None,
                        incident_id=None,
                        created_at=now,
                    )

        # HTTP/TCP/DNS/SCRIPT checks are actively polled according to `interval` and `next_run`.
        elif check.type in (CheckType.HTTP, CheckType.TCP, CheckType.DNS, CheckType.SCRIPT):
            timeout = check.timeout or 5
            retries = check.retries or 1
            interval = getattr(check, "interval", None) or 60
            if check.next_run is not None and now < check.next_run:
                continue

            ok = False
            reason = "unknown"
            latency_ms = None
            evidence_status = CheckStatus.UP
            evidence_error: Optional[str] = None
            evidence_incident_id: Optional[int] = None
            if check.type == CheckType.HTTP:
                if not check.url:
                    continue
                ok, reason, latency_ms = _http_check(check.url, timeout, retries)
            elif check.type == CheckType.TCP:
                if not check.host or not check.port:
                    continue
                ok, reason, latency_ms = _tcp_check(check.host, check.port, timeout)
            elif check.type == CheckType.DNS:
                if not check.host:
                    continue
                ok, reason, latency_ms = _dns_check(check.host, check.dns_record_type)
            elif check.type == CheckType.SCRIPT:
                ok, reason, latency_ms = _script_check(check, project, timeout, retries)

            if ok:
                check.last_ping = now
                check.last_latency_ms = latency_ms
                is_degraded = _is_degraded(check, latency_ms)
                if is_degraded:
                    evidence_status = CheckStatus.DEGRADED
                    evidence_error = (
                        f"latency threshold exceeded ({latency_ms:.1f}ms)"
                        if latency_ms is not None
                        else "latency threshold exceeded"
                    )
                    # degrade state
                    if check.status != CheckStatus.DEGRADED:
                        # close open incident if exists
                        open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                        if open_inc:
                            open_inc.resolved_at = now
                            open_inc.status = "resolved"
                            session.add(open_inc)
                            session.commit()
                        check.status = CheckStatus.DEGRADED
                        check.consecutive_failures = 0
                        event_message = f"latency_ms={latency_ms:.1f}" if latency_ms is not None else "degraded"
                        if maintenance:
                            event_message = f"{event_message} (suppressed due to maintenance)"
                        event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DEGRADED, message=event_message)
                        session.add(event)
                        if not maintenance:
                            _trigger_remediation(session, project, check, EventType.DEGRADED, event.message, now)
                            _ensure_oncall_alert(session, project, check, EventType.DEGRADED, event.message, now)
                        if check.alert_enabled and not maintenance:
                            flapping_suppressed = _should_suppress_alert_due_to_flapping(
                                session,
                                project,
                                check,
                                now,
                                EventType.DEGRADED,
                                reason=f"latency_ms={latency_ms:.1f}" if latency_ms is not None else None,
                            )
                            last_alert = check.last_alerted_at
                            cooldown = check.alert_cooldown or 0
                            if (not flapping_suppressed) and ((last_alert is None) or ((now - last_alert).total_seconds() > cooldown)):
                                session.add(check)
                                session.commit()
                                try:
                                    notify_degraded(check, project, reason=f"latency_ms={latency_ms:.1f}" if latency_ms is not None else None)
                                    check.last_alerted_at = now
                                    check.last_alert_type = EventType.DEGRADED
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending DEGRADED alert")
                    else:
                        # still degraded: allow re-alerts after cooldown
                        if check.alert_enabled and not maintenance:
                            flapping_suppressed = _should_suppress_alert_due_to_flapping(
                                session,
                                project,
                                check,
                                now,
                                EventType.DEGRADED,
                                reason="still degraded",
                            )
                            last_alert = check.last_alerted_at
                            cooldown = check.alert_cooldown or 0
                            if (not flapping_suppressed) and ((last_alert is None) or ((now - last_alert).total_seconds() > cooldown)):
                                session.add(check)
                                session.commit()
                                try:
                                    notify_degraded(check, project, reason="still degraded")
                                    check.last_alerted_at = now
                                    check.last_alert_type = EventType.DEGRADED
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending repeated DEGRADED alert")
                else:
                    evidence_status = CheckStatus.UP
                    evidence_error = None
                    if check.status in (CheckStatus.DOWN, CheckStatus.DEGRADED):
                        check.status = CheckStatus.UP
                        check.consecutive_failures = 0
                        open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                        evidence_incident_id = open_inc.id if open_inc else None
                        if open_inc:
                            open_inc.resolved_at = now
                            open_inc.status = "resolved"
                            session.add(open_inc)
                            session.commit()
                        event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.UP, message=f"check ok ({reason})", incident_id=(open_inc.id if open_inc else None))
                        session.add(event)
                        _close_oncall_alerts(session, check.id)
                        if check.alert_enabled and not maintenance:
                            flapping_suppressed = _should_suppress_alert_due_to_flapping(
                                session,
                                project,
                                check,
                                now,
                                EventType.UP,
                                reason=f"check ok ({reason})",
                            )
                            last_alert = check.last_alerted_at
                            cooldown = check.alert_cooldown or 0
                            if (not flapping_suppressed) and ((last_alert is None) or ((now - last_alert).total_seconds() > cooldown)):
                                session.add(check)
                                session.commit()
                                try:
                                    notify_recovery(check, project)
                                    check.last_alerted_at = now
                                    check.last_alert_type = EventType.UP
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending recovery alert")
                    else:
                        check.consecutive_failures = 0
                        session.add(check)
                        session.commit()
            else:
                # failure
                evidence_status = CheckStatus.DOWN
                evidence_error = reason
                check.consecutive_failures = (check.consecutive_failures or 0) + 1
                open_inc = session.exec(
                    select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)
                ).first()
                evidence_incident_id = open_inc.id if open_inc else None
                event_type = EventType.HTTP_FAILURE if check.type == CheckType.HTTP else EventType.DOWN
                if check.status != CheckStatus.DOWN:
                    check.status = CheckStatus.DOWN
                    created_incident = False
                    if not open_inc:
                        signature = _normalize_reason(reason)
                        group_root = _find_group_root(session, check.project_id, signature, now)
                        open_inc = Incident(project_id=check.project_id, check_id=check.id, started_at=now, status="open", group_id=group_root)
                        session.add(open_inc)
                        session.commit()
                        session.refresh(open_inc)
                        created_incident = True
                        evidence_incident_id = open_inc.id
                    if created_incident:
                        _log_similar_incidents(session, project, open_inc.id, reason)
                    event_message = f"{reason}"
                    if maintenance:
                        event_message = f"{event_message} (suppressed due to maintenance)"
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=event_type, message=event_message, incident_id=open_inc.id)
                    session.add(event)
                    rem_event = EventType.DOWN if event_type == EventType.HTTP_FAILURE else event_type
                    if not maintenance:
                        _trigger_remediation(session, project, check, rem_event, reason, now)
                        _ensure_oncall_alert(session, project, check, rem_event, reason, now)
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    flapping_suppressed = False
                    if should_alert and not maintenance:
                        flapping_suppressed = _should_suppress_alert_due_to_flapping(
                            session,
                            project,
                            check,
                            now,
                            event_type,
                            reason=reason,
                        )
                    if should_alert:
                        if maintenance or flapping_suppressed:
                            session.add(check)
                            session.commit()
                        else:
                            throttled = _project_is_throttled(session, project, now)
                            if throttled:
                                _trigger_escalation(session, project, now, reason, check=check)
                                session.add(check)
                                session.commit()
                            else:
                                last_alert = check.last_alerted_at
                                cooldown = check.alert_cooldown or 0
                                if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
                                    session.add(check)
                                    session.commit()
                                    try:
                                        notify_down(check, project, reason=reason)
                                        check.last_alerted_at = now
                                        check.last_alert_type = event_type
                                        session.add(check)
                                        session.commit()
                                    except Exception:
                                        logger.exception("Error sending DOWN alert")
                            _trigger_check_escalation(session, project, check, open_inc, now, reason)
                    else:
                        session.add(check)
                        session.commit()
                else:
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    flapping_suppressed = False
                    if should_alert and not maintenance:
                        flapping_suppressed = _should_suppress_alert_due_to_flapping(
                            session,
                            project,
                            check,
                            now,
                            event_type,
                            reason="still down",
                        )
                    if should_alert:
                        if maintenance or flapping_suppressed:
                            session.add(check)
                            session.commit()
                        else:
                            throttled = _project_is_throttled(session, project, now)
                            if throttled:
                                _trigger_escalation(session, project, now, "still down", check=check)
                                session.add(check)
                                session.commit()
                            else:
                                last_alert = check.last_alerted_at
                                cooldown = check.alert_cooldown or 0
                                if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
                                    session.add(check)
                                    session.commit()
                                    try:
                                        notify_down(check, project, reason="still down")
                                        check.last_alerted_at = now
                                        check.last_alert_type = event_type
                                        session.add(check)
                                        session.commit()
                                    except Exception:
                                        logger.exception("Error sending repeated DOWN alert")
                            _trigger_check_escalation(session, project, check, open_inc, now, "still down")
                    else:
                        session.add(check)
                        session.commit()
            _record_check_result(
                session,
                check,
                run_key=run_key,
                status=evidence_status,
                latency_ms=latency_ms,
                error_message=evidence_error,
                incident_id=evidence_incident_id,
                created_at=now,
            )
            try:
                check.next_run = now + timedelta(seconds=interval)
                session.add(check)
                session.commit()
            except Exception:
                logger.exception("Error persisting next_run for check %s", getattr(check, 'id', None))

        if not processed_oncall:
            processed_oncall = True
            try:
                _process_oncall_alerts(session, now)
            except Exception:
                logger.exception("Error processing on-call alerts")
        if not processed_remediation:
            processed_remediation = True
            try:
                _process_remediation_approvals(session, now)
            except Exception:
                logger.exception("Error processing remediation approvals")

    if not processed_oncall:
        try:
            _process_oncall_alerts(session, now)
        except Exception:
            logger.exception("Error processing on-call alerts")
    if not processed_remediation:
        try:
            _process_remediation_approvals(session, now)
        except Exception:
            logger.exception("Error processing remediation approvals")
    try:
        _maybe_archive_monthly_rollups(session, now, project_ids)
    except Exception:
        logger.exception("Error archiving monthly rollups")
    try:
        _maybe_archive_quarterly_rollups(session, now, project_ids)
    except Exception:
        logger.exception("Error archiving quarterly rollups")
    try:
        _maybe_log_rollup_archive_health(session, now, project_ids)
    except Exception:
        logger.exception("Error checking rollup archive health")
    try:
        _maybe_log_predictive_warnings(session, now, project_ids)
    except Exception:
        logger.exception("Error computing predictive warnings")
    try:
        _maybe_log_early_warnings(session, now, project_ids)
    except Exception:
        logger.exception("Error computing early warnings")
    # After processing checks, compute a short-term uptime/MTTR snapshot (last 24h)
    try:
        _compute_uptime_snapshots(session, now)
    except Exception:
        logger.exception("Error computing uptime snapshots")


def main():
    scan_interval = int(os.environ.get("WORKER_SCAN_INTERVAL", "30"))
    logger.info("Starting LastPing worker; scan interval=%s seconds", scan_interval)
    try:
        while True:
            with Session(engine) as session:
                try:
                    scan_checks_once(session)
                except Exception:
                    logger.exception("Error scanning checks")
            # update worker health file so orchestration can determine liveness
            health_file = os.environ.get("WORKER_HEALTH_FILE", "/tmp/lastping_worker.health")
            try:
                with open(health_file, "w") as hf:
                    hf.write(datetime.utcnow().isoformat() + "\n")
            except Exception:
                logger.exception("Failed to write worker health file")

            time.sleep(scan_interval)
    except KeyboardInterrupt:
        logger.info("Worker stopping")


if __name__ == "__main__":
    main()

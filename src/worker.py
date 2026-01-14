import os
import time
import logging
"""
Background worker: scans checks and executes monitoring logic.

This module contains the worker loop and core detection behaviour used
to mark checks UP/DOWN, persist events, and call alerting functions.
Keep scan logic deterministic and side-effect minimal so tests remain
stable.
"""

from datetime import datetime, timedelta
import urllib.request
import urllib.error
from typing import Tuple

from sqlmodel import Session, select

from .db import engine
from .models import Check, CheckType, CheckStatus, Event, EventType, Project, Incident
from .alerts import notify_down, notify_recovery
from .models import UptimeSnapshot

logger = logging.getLogger("lastping.worker")


def _now() -> datetime:
    """Return current UTC datetime (extracted to ease testing)."""
    return datetime.utcnow()


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


def _http_check(url: str, timeout: int, retries: int) -> Tuple[bool, str]:
    """Perform a simple HTTP GET with retries.

    Returns (ok: bool, reason: str). The reason is a short diagnostic
    such as `status=200` or an error string for logging/alerting.
    """
    last_exc = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LastPing/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                if 200 <= code < 300:
                    return True, f"status={code}"
                else:
                    last_exc = f"status={code}"
        except urllib.error.HTTPError as he:
            # record HTTP errors (4xx/5xx) as the last exception reason
            last_exc = f"http_error={getattr(he, 'code', 'unknown')}"
        except Exception as e:
            last_exc = str(e)
        # small backoff between retries
        time.sleep(0.5)
    return False, last_exc or "unknown"


def _project_is_throttled(session: Session, project: Project, now: datetime) -> bool:
    pr_count = getattr(project, "alert_rate_limit_count", 0) or 0
    pr_window = getattr(project, "alert_rate_limit_window", 0) or 0
    if not pr_count or not pr_window:
        return False
    cutoff = now - timedelta(seconds=pr_window)
    recent_events = session.exec(select(Event).where(Event.project_id == project.id, Event.created_at > cutoff)).all()
    recent_bad = [e for e in recent_events if e.event_type in (EventType.DOWN, EventType.HTTP_FAILURE)]
    return len(recent_bad) >= pr_count


def _trigger_escalation(session: Session, project: Project, now: datetime, reason: str):
    try:
        from .alerts import notify_escalation

        last_es = getattr(project, "last_escalated_at", None)
        window = getattr(project, "alert_rate_limit_window", 0) or 0
        if (last_es is None) or ((now - last_es).total_seconds() > window):
            ok = notify_escalation(project, reason)
            project.last_escalated_at = now
            session.add(project)
            session.commit()
            return ok
    except Exception:
        logger.exception("Error triggering escalation")
    return False


def scan_checks_once(session: Session):
    # Load all checks and process each synchronously. The worker is
    # intentionally simple (single-threaded) to avoid race conditions
    # with the DB and to make behaviour easy to reason about in tests.
    stmt = select(Check)
    results = session.exec(stmt).all()
    now = _now()
    for check in results:
        project = session.get(Project, check.project_id)

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
                    # find or create open incident for this check
                    open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                    if not open_inc:
                        open_inc = Incident(project_id=check.project_id, check_id=check.id, started_at=now, status="open")
                        session.add(open_inc)
                        session.commit()
                        session.refresh(open_inc)
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DOWN, message="missed heartbeat", incident_id=open_inc.id)
                    session.add(event)
                    # alerting: only send if enabled and threshold reached and cooldown passed
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    if should_alert:
                        # suppress alerts during a maintenance window
                        if _in_maintenance(check, project, now):
                            event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DOWN, message="missed heartbeat (suppressed due to maintenance)", incident_id=(open_inc.id if 'open_inc' in locals() and open_inc else None))
                            session.add(event)
                            session.add(check)
                            session.commit()
                            continue
                        # project-level throttling/escalation
                        throttled = _project_is_throttled(session, project, now)
                        if throttled:
                            _trigger_escalation(session, project, now, "missed heartbeat")
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
                    else:
                        session.add(check)
                        session.commit()
                else:
                    check.consecutive_failures = (check.consecutive_failures or 0) + 1
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
                    # recovery alert: respect cooldown and enabled
                    if check.alert_enabled:
                        last_alert = check.last_alerted_at
                        cooldown = check.alert_cooldown or 0
                        if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
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

        # HTTP checks are actively polled according to `interval` and
        # `next_run`. The worker executes `_http_check`, updates status
        # and persists `next_run` for scheduling.
        elif check.type == CheckType.HTTP:
            if not check.url:
                continue
            timeout = check.timeout or 5
            retries = check.retries or 1
            # scheduling: skip until next_run for HTTP checks
            interval = getattr(check, "interval", None) or 60
            if check.next_run is not None and now < check.next_run:
                # not yet time to run this check
                continue
            ok, reason = _http_check(check.url, timeout, retries)
            if ok:
                check.last_ping = now
                if check.status == CheckStatus.DOWN:
                    check.status = CheckStatus.UP
                    check.consecutive_failures = 0
                    # close incident if exists
                    open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                    if open_inc:
                        open_inc.resolved_at = now
                        open_inc.status = "resolved"
                        session.add(open_inc)
                        session.commit()
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.UP, message=f"http success ({reason})", incident_id=(open_inc.id if open_inc else None))
                    session.add(event)
                    # recovery alert: respect cooldown and enabled
                    if check.alert_enabled:
                        last_alert = check.last_alerted_at
                        cooldown = check.alert_cooldown or 0
                        if (last_alert is None) or ((now - last_alert).total_seconds() > cooldown):
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
                    check.consecutive_failures = 0
                    session.add(check)
                    session.commit()
            else:
                # failure
                check.consecutive_failures = (check.consecutive_failures or 0) + 1
                if check.status != CheckStatus.DOWN:
                    check.status = CheckStatus.DOWN
                    # find or create open incident for this check
                    open_inc = session.exec(select(Incident).where(Incident.check_id == check.id, Incident.resolved_at == None)).first()
                    if not open_inc:
                        open_inc = Incident(project_id=check.project_id, check_id=check.id, started_at=now, status="open")
                        session.add(open_inc)
                        session.commit()
                        session.refresh(open_inc)
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.HTTP_FAILURE, message=f"{reason}", incident_id=open_inc.id)
                    session.add(event)
                    should_alert = check.alert_enabled and (check.consecutive_failures >= (check.alert_after or 1))
                    if should_alert:
                        # suppress alerts during a maintenance window
                        if _in_maintenance(check, project, now):
                            event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.HTTP_FAILURE, message=f"{reason} (suppressed due to maintenance)")
                            session.add(event)
                            session.add(check)
                            session.commit()
                            # persist next_run below as usual
                            try:
                                check.next_run = now + timedelta(seconds=interval)
                                session.add(check)
                                session.commit()
                            except Exception:
                                logger.exception("Error persisting next_run for check %s", getattr(check, 'id', None))
                            continue
                        throttled = _project_is_throttled(session, project, now)
                        if throttled:
                            _trigger_escalation(session, project, now, reason)
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
                                    check.last_alert_type = EventType.HTTP_FAILURE
                                    session.add(check)
                                    session.commit()
                                except Exception:
                                    logger.exception("Error sending DOWN alert")
                            else:
                                session.add(check)
                                session.commit()
                    else:
                        session.add(check)
                        session.commit()
                else:
                    session.add(check)
                    session.commit()
            # persist next_run after executing the HTTP check
            try:
                check.next_run = now + timedelta(seconds=interval)
                session.add(check)
                session.commit()
            except Exception:
                logger.exception("Error persisting next_run for check %s", getattr(check, 'id', None))

        # After processing checks, compute a short-term uptime/MTTR snapshot (last 24h)
        try:
            window_end = now
            window_start = now - timedelta(hours=24)
            # for each check, compute uptime and mttr similar to metrics logic
            stmt = select(Check)
            all_checks = session.exec(stmt).all()
            for c in all_checks:
                # load events in window
                ev_stmt = select(Event).where(Event.project_id == c.project_id, Event.check_id == c.id, Event.created_at >= window_start, Event.created_at <= window_end).order_by(Event.created_at)
                events = session.exec(ev_stmt).all()
                # determine initial state
                prev_stmt = select(Event).where(Event.project_id == c.project_id, Event.check_id == c.id, Event.created_at < window_start).order_by(Event.created_at.desc())
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

                # compute MTTR for the window
                downs = []
                for i, ev in enumerate(events):
                    if ev.event_type in ("down", "http_failure"):
                        for j in range(i+1, len(events)):
                            if events[j].event_type == "up":
                                dur = (events[j].created_at - ev.created_at).total_seconds()
                                downs.append(dur)
                                break
                mttr = None
                if downs:
                    mttr = sum(downs) / len(downs)

                snap = UptimeSnapshot(project_id=c.project_id, check_id=c.id, window_start=window_start, window_end=window_end, uptime_percent=uptime_pct, mttr_seconds=mttr)
                try:
                    session.add(snap)
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception("Failed to persist uptime snapshot for check %s", c.id)
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

import os
import time
import logging
from datetime import datetime, timedelta
import urllib.request
import urllib.error

from sqlmodel import Session, select

from .db import engine
from .models import Check, CheckType, CheckStatus, Event, EventType, Project
from .alerts import notify_down, notify_recovery

logger = logging.getLogger("lastping.worker")


def _now() -> datetime:
    return datetime.utcnow()


def _http_check(url: str, timeout: int, retries: int) -> (bool, str):
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
            last_exc = f"http_error={getattr(he, 'code', 'unknown')}"
        except Exception as e:
            last_exc = str(e)
        # small backoff between retries
        time.sleep(0.5)
    return False, last_exc or "unknown"


def scan_checks_once(session: Session):
    stmt = select(Check)
    results = session.exec(stmt).all()
    now = _now()
    for check in results:
        project = session.get(Project, check.project_id)

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
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DOWN, message="missed heartbeat")
                    session.add(event)
                    session.add(check)
                    session.commit()
                    try:
                        notify_down(check, project, reason="missed heartbeat")
                    except Exception:
                        logger.exception("Error sending DOWN alert")
                else:
                    check.consecutive_failures = (check.consecutive_failures or 0) + 1
                    session.add(check)
                    session.commit()
            else:
                if check.status == CheckStatus.DOWN:
                    check.status = CheckStatus.UP
                    check.consecutive_failures = 0
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.UP, message="recovered")
                    session.add(event)
                    session.add(check)
                    session.commit()
                    try:
                        notify_recovery(check, project)
                    except Exception:
                        logger.exception("Error sending recovery alert")

        elif check.type == CheckType.HTTP:
            if not check.url:
                continue
            timeout = check.timeout or 5
            retries = check.retries or 1
            ok, reason = _http_check(check.url, timeout, retries)
            if ok:
                check.last_ping = now
                if check.status == CheckStatus.DOWN:
                    check.status = CheckStatus.UP
                    check.consecutive_failures = 0
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.UP, message=f"http success ({reason})")
                    session.add(event)
                    session.add(check)
                    session.commit()
                    try:
                        notify_recovery(check, project)
                    except Exception:
                        logger.exception("Error sending recovery alert")
                else:
                    check.consecutive_failures = 0
                    session.add(check)
                    session.commit()
            else:
                # failure
                check.consecutive_failures = (check.consecutive_failures or 0) + 1
                if check.status != CheckStatus.DOWN:
                    check.status = CheckStatus.DOWN
                    event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.HTTP_FAILURE, message=f"{reason}")
                    session.add(event)
                    session.add(check)
                    session.commit()
                    try:
                        notify_down(check, project, reason=reason)
                    except Exception:
                        logger.exception("Error sending DOWN alert")
                else:
                    session.add(check)
                    session.commit()


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
            time.sleep(scan_interval)
    except KeyboardInterrupt:
        logger.info("Worker stopping")


if __name__ == "__main__":
    main()

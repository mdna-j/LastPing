import os
import time
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .db import engine
from .models import Check, CheckType, CheckStatus, Event, EventType, Project
from .alerts import notify_down, notify_recovery

logger = logging.getLogger("lastping.worker")


def _now() -> datetime:
    return datetime.utcnow()


def scan_checks_once(session: Session):
    stmt = select(Check)
    results = session.exec(stmt).all()
    now = _now()
    for check in results:
        # Only handle heartbeat checks for now
        if check.type != CheckType.HEARTBEAT:
            continue

        expected = check.expected_interval or 600
        grace = check.grace_period or 600

        last_ping = check.last_ping or getattr(check, "created_at", None)
        if last_ping is None:
            # If there is no last ping or created_at, skip
            continue

        threshold = last_ping + timedelta(seconds=(expected + grace))

        # Get project info for alert messages
        project = session.get(Project, check.project_id)

        if now > threshold:
            # Overdue
            if check.status != CheckStatus.DOWN:
                logger.info("Marking check %s (id=%s) DOWN", check.name, check.id)
                check.status = CheckStatus.DOWN
                check.consecutive_failures = (check.consecutive_failures or 0) + 1
                event = Event(check_id=check.id, project_id=check.project_id, event_type=EventType.DOWN, message="missed heartbeat")
                session.add(event)
                session.add(check)
                session.commit()
                try:
                    notify_down(check, project)
                except Exception:
                    logger.exception("Error sending DOWN alert")
            else:
                # already DOWN — increment failures counter occasionally
                check.consecutive_failures = (check.consecutive_failures or 0) + 1
                session.add(check)
                session.commit()
        else:
            # Within threshold -> if previously DOWN, mark UP (recovery)
            if check.status == CheckStatus.DOWN:
                logger.info("Check %s (id=%s) recovered UP", check.name, check.id)
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

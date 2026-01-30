import os
import json
from datetime import datetime, timedelta


def test_rollup_archive_alerts(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'rollup_alerts.db'}"
    os.environ["ROLLUP_ARCHIVE_ALERTS_ENABLED"] = "1"
    os.environ["ROLLUP_ARCHIVE_GRACE_DAYS"] = "0"
    os.environ["ROLLUP_QUARTERLY_GRACE_DAYS"] = "0"
    os.environ["ROLLUP_ARCHIVE_ALERT_WINDOW_SECONDS"] = "0"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, AuditLog
    from src.security import hash_api_key
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="rollup_proj", api_key_hash=hash_api_key("rkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        now = datetime.utcnow() + timedelta(days=40)
        worker._maybe_log_rollup_archive_health(session, now, {project.id})

        logs = session.exec(select(AuditLog).where(AuditLog.action == "rollup_archive_missing")).all()
        assert len(logs) >= 1
        details = json.loads(logs[0].details or "{}")
        assert details.get("period_type") in ("month", "quarter")

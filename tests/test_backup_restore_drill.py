import os
from datetime import datetime


def test_sqlite_backup_restore_drill_round_trip(tmp_path):
    source_db = tmp_path / "source_restore_drill.sqlite"
    restore_db = tmp_path / "restored_restore_drill.sqlite"
    output_dir = tmp_path / "artifacts"
    os.environ["DATABASE_URL"] = f"sqlite:///{source_db}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.backup_restore import run_restore_drill
    from src.models import (
        AuditLog,
        Check,
        CheckLease,
        CheckResult,
        CheckStatus,
        CheckType,
        Event,
        EventType,
        Incident,
        Project,
        StatusSubscription,
    )

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="restore-drill")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api-heartbeat", type=CheckType.HEARTBEAT, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(project_id=project.id, check_id=check.id, started_at=datetime.utcnow())
        session.add(incident)
        session.commit()
        session.refresh(incident)

        session.add(
            Event(
                project_id=project.id,
                check_id=check.id,
                incident_id=incident.id,
                event_type=EventType.DOWN,
                message="restore drill synthetic failure",
            )
        )
        session.add(
            CheckResult(
                project_id=project.id,
                check_id=check.id,
                incident_id=incident.id,
                status=CheckStatus.DOWN,
                latency_ms=912.4,
                error_message="timeout",
            )
        )
        session.add(
            AuditLog(
                actor="restore-drill",
                action="seed_restore_drill",
                project_id=project.id,
                target_type="project",
                target_id=project.id,
            )
        )
        session.add(StatusSubscription(project_id=project.id, channel="email", target="ops@example.com"))
        session.add(CheckLease(check_id=check.id, lease_owner="worker-us", lease_fence=2))
        session.commit()

    summary = run_restore_drill(
        f"sqlite:///{source_db}",
        f"sqlite:///{restore_db}",
        output_dir=output_dir,
    )

    assert summary["status"] == "ok"
    assert summary["mismatched_tables"] == {}
    assert summary["table_counts_before"]["project"] == 1
    assert summary["table_counts_before"]["check"] == 1
    assert summary["table_counts_before"]["incident"] == 1
    assert summary["table_counts_before"]["event"] == 1
    assert summary["table_counts_before"]["check_result"] == 1
    assert summary["table_counts_before"]["audit_log"] == 1
    assert summary["table_counts_before"]["status_subscription"] == 1
    assert summary["table_counts_before"]["check_lease"] == 1
    assert summary["table_counts_after"] == summary["table_counts_before"]
    assert summary["backup_kept"] is False
    assert summary["alembic_version_after"] is not None
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "summary.md").exists()
    assert not any(path.suffix == ".sqlite3" for path in output_dir.iterdir())

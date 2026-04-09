import os
from datetime import datetime, timedelta


def test_worker_notifies_public_status_subscribers_for_open_and_resolve(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_status_notifications.sqlite'}"

    from sqlmodel import Session

    from src import alerts, db as dbmod, worker
    from src.models import Check, CheckStatus, CheckType, Project, StatusSubscription

    dbmod.create_db_and_tables()

    deliveries = []

    monkeypatch.setattr(worker, "notify_down", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "notify_recovery", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        alerts,
        "send_email",
        lambda subject, body, to=None: deliveries.append(("email", subject, to, body)) or True,
    )

    with Session(dbmod.engine) as session:
        project = Project(name="status-notify-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="heartbeat-api",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(hours=2),
            status=CheckStatus.UP,
        )
        session.add(check)
        session.add(StatusSubscription(project_id=project.id, channel="email", target="ops@example.com"))
        session.add(StatusSubscription(project_id=project.id, channel="webhook", target="https://example.com/status"))
        session.commit()
        session.refresh(check)

        worker.scan_checks_once(session)
        session.refresh(check)
        assert check.status == CheckStatus.DOWN

        assert any(item[0] == "email" and "incident opened" in item[1].lower() for item in deliveries)
        assert not any(item[0] == "webhook" for item in deliveries)

        check.last_ping = datetime.utcnow()
        session.add(check)
        session.commit()

        worker.scan_checks_once(session)
        session.refresh(check)
        assert check.status == CheckStatus.UP

        assert any(item[0] == "email" and "incident resolved" in item[1].lower() for item in deliveries)
        assert not any(item[0] == "webhook" for item in deliveries)

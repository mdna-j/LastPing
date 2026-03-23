import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_public_status_page_exposes_components_history_and_subscriptions(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_public_status.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src.models import (
        Check,
        CheckStatus,
        CheckType,
        Event,
        EventType,
        Incident,
        Project,
        StatusSubscription,
    )

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="status-public-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        api_check = Check(
            project_id=project.id,
            name="api",
            type=CheckType.HTTP,
            status=CheckStatus.DOWN,
            url="https://example.com/health",
            region="us-east-1",
            last_ping=datetime.utcnow() - timedelta(minutes=8),
        )
        worker_check = Check(
            project_id=project.id,
            name="worker",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.UP,
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(seconds=25),
        )
        session.add(api_check)
        session.add(worker_check)
        session.commit()
        session.refresh(api_check)
        session.refresh(worker_check)

        open_incident = Incident(
            project_id=project.id,
            check_id=api_check.id,
            started_at=datetime.utcnow() - timedelta(minutes=11),
            status="open",
        )
        resolved_incident = Incident(
            project_id=project.id,
            check_id=worker_check.id,
            started_at=datetime.utcnow() - timedelta(hours=2),
            resolved_at=datetime.utcnow() - timedelta(hours=1, minutes=35),
            status="resolved",
        )
        session.add(open_incident)
        session.add(resolved_incident)
        session.commit()
        session.refresh(open_incident)
        session.refresh(resolved_incident)

        session.add(
            Event(
                check_id=api_check.id,
                project_id=project.id,
                incident_id=open_incident.id,
                event_type=EventType.DOWN,
                message="api timed out",
                created_at=datetime.utcnow() - timedelta(minutes=10),
            )
        )
        session.add(
            Event(
                check_id=worker_check.id,
                project_id=project.id,
                incident_id=resolved_incident.id,
                event_type=EventType.UP,
                message="worker recovered",
                created_at=datetime.utcnow() - timedelta(hours=1, minutes=34),
            )
        )
        session.commit()

        project_id = project.id

    page = client.get(f"/ui/status/{project_id}")
    assert page.status_code == 200
    assert "System Status" in page.text
    assert "Live component health" in page.text

    data = client.get(f"/ui/status/{project_id}/data")
    assert data.status_code == 200
    payload = data.json()
    assert payload["project"]["id"] == project_id
    assert payload["summary"]["overall_status"] == "major_outage"
    assert payload["summary"]["open_incident_count"] == 1
    assert len(payload["components"]) == 2
    assert any(component["incident_open"] for component in payload["components"])
    assert len(payload["incident_history"]) == 2
    assert any(incident["resolved_at"] is not None for incident in payload["incident_history"])

    subscribe = client.post(
        f"/ui/status/{project_id}/subscribe",
        json={"channel": "email", "target": "status@example.com"},
    )
    assert subscribe.status_code == 200
    assert subscribe.json()["subscription"]["channel"] == "email"

    subscribe_again = client.post(
        f"/ui/status/{project_id}/subscribe",
        json={"channel": "email", "target": "status@example.com"},
    )
    assert subscribe_again.status_code == 200
    assert "reactivated" in subscribe_again.json()["message"].lower()

    bad_webhook = client.post(
        f"/ui/status/{project_id}/subscribe",
        json={"channel": "webhook", "target": "not-a-url"},
    )
    assert bad_webhook.status_code == 422

    with Session(dbmod.engine) as session:
        rows = session.exec(select(StatusSubscription).where(StatusSubscription.project_id == project_id)).all()
        assert len(rows) == 1
        assert rows[0].target == "status@example.com"

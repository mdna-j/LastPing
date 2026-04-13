import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_pagerduty_webhook_syncs_incident_lifecycle_and_notes(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_webhook.sqlite'}"
    os.environ["PAGERDUTY_WEBHOOK_SECRET"] = "pd-secret"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Check, CheckType, Incident, IncidentNote, Project

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="pd-sync-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, url="https://example.com")
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            status="open",
            pagerduty_dedup_key=f"lastping:incident:{project.id}:77",
            started_at=datetime.utcnow() - timedelta(minutes=15),
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        project_id = project.id
        incident_id = incident.id

    payload = {
        "messages": [
            {
                "event_type": "incident.acknowledged",
                "dedup_key": f"lastping:incident:{project_id}:77",
                "occurred_at": "2026-04-03T17:10:00Z",
                "agent": {"summary": "PD Responder"},
            },
            {
                "event_type": "incident.reassigned",
                "dedup_key": f"lastping:incident:{project_id}:77",
                "occurred_at": "2026-04-03T17:11:00Z",
                "agent": {"summary": "PD Manager"},
                "data": {"assignees": [{"summary": "Primary On-Call"}]},
            },
            {
                "event_type": "incident.annotated",
                "dedup_key": f"lastping:incident:{project_id}:77",
                "occurred_at": "2026-04-03T17:12:00Z",
                "agent": {"summary": "PD Responder"},
                "data": {"body": {"details": "Upstream dependency saturation."}},
            },
            {
                "event_type": "incident.resolved",
                "dedup_key": f"lastping:incident:{project_id}:77",
                "occurred_at": "2026-04-03T17:13:00Z",
                "agent": {"summary": "PD Responder"},
            },
        ]
    }

    resp = client.post(
        "/integrations/pagerduty/webhook",
        json=payload,
        headers={"X-PagerDuty-Webhook-Secret": "pd-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"accepted": True, "processed": 4, "changed": 4, "ignored": 0}

    with Session(dbmod.engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.acknowledged_by == "pagerduty:PD Responder"
        assert incident.owner == "Primary On-Call"
        assert incident.status == "resolved"
        assert incident.resolved_at is not None
        assert incident.resolved_by == "pagerduty:PD Responder"
        assert incident.resolution_summary == "Resolved in PagerDuty by PD Responder."

        notes = session.exec(select(IncidentNote).where(IncidentNote.incident_id == incident_id)).all()
        assert len(notes) == 1
        assert notes[0].author == "pagerduty:PD Responder"
        assert notes[0].body == "Upstream dependency saturation."

        actions = [
            log.action
            for log in session.exec(
                select(AuditLog).where(AuditLog.target_type == "incident", AuditLog.target_id == incident_id)
            ).all()
        ]
        assert "pagerduty_ack" in actions
        assert "pagerduty_assign" in actions
        assert "pagerduty_note" in actions
        assert "pagerduty_resolve" in actions


def test_pagerduty_webhook_can_reopen_and_clear_ack(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_webhook_reopen.sqlite'}"
    os.environ["PAGERDUTY_WEBHOOK_SECRET"] = "pd-secret"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, CheckType, Incident, Project

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="pd-reopen-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, url="https://example.com")
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            status="resolved",
            resolved_at=datetime.utcnow() - timedelta(minutes=2),
            resolved_by="pagerduty:Old User",
            resolution_summary="Old summary",
            acknowledged_at=datetime.utcnow() - timedelta(minutes=3),
            acknowledged_by="pagerduty:Old User",
            pagerduty_dedup_key=f"lastping:incident:{project.id}:88",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        project_id = project.id
        incident_id = incident.id

    clear_ack = client.post(
        "/integrations/pagerduty/webhook",
        json={
            "event": {
                "event_type": "incident.unacknowledged",
                "dedup_key": f"lastping:incident:{project_id}:88",
                "agent": {"summary": "PD Responder"},
            }
        },
        headers={"X-PagerDuty-Webhook-Secret": "pd-secret"},
    )
    assert clear_ack.status_code == 200

    reopen = client.post(
        "/integrations/pagerduty/webhook",
        json={
            "event": {
                "event_type": "incident.reopened",
                "dedup_key": f"lastping:incident:{project_id}:88",
                "agent": {"summary": "PD Responder"},
            }
        },
        headers={"X-PagerDuty-Webhook-Secret": "pd-secret"},
    )
    assert reopen.status_code == 200

    with Session(dbmod.engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.acknowledged_at is None
        assert incident.acknowledged_by is None
        assert incident.status == "open"
        assert incident.resolved_at is None
        assert incident.resolved_by is None
        assert incident.resolution_summary is None


def test_pagerduty_webhook_requires_shared_secret(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_webhook_auth.sqlite'}"
    os.environ["PAGERDUTY_WEBHOOK_SECRET"] = "pd-secret"

    from src.main import app

    client = TestClient(app)

    missing = client.post("/integrations/pagerduty/webhook", json={"event": {"event_type": "incident.acknowledged"}})
    assert missing.status_code == 401

    invalid = client.post(
        "/integrations/pagerduty/webhook",
        json={"event": {"event_type": "incident.acknowledged"}},
        headers={"X-PagerDuty-Webhook-Secret": "wrong"},
    )
    assert invalid.status_code == 403

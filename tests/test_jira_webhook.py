import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_jira_webhook_syncs_comment_assignee_and_status(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_webhook.sqlite'}"
    os.environ["JIRA_WEBHOOK_SECRET"] = "jira-secret"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Check, CheckType, Incident, IncidentNote, Project

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="jira-sync-project")
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
            jira_issue_key="OPS-17",
            jira_issue_url="https://lastping.atlassian.net/browse/OPS-17",
            started_at=datetime.utcnow() - timedelta(minutes=10),
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        incident_id = incident.id

    comment_resp = client.post(
        "/integrations/jira/webhook",
        json={
            "webhookEvent": "comment_created",
            "timestamp": "2026-04-03T17:10:00Z",
            "user": {"displayName": "Jira Responder"},
            "issue": {"key": "OPS-17"},
            "comment": {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Investigating upstream timeout."}]}]}},
        },
        headers={"X-Jira-Webhook-Secret": "jira-secret"},
    )
    assert comment_resp.status_code == 200
    assert comment_resp.json() == {"accepted": True, "processed": 1, "changed": 1, "ignored": 0}

    update_resp = client.post(
        "/integrations/jira/webhook",
        json={
            "webhookEvent": "jira:issue_updated",
            "timestamp": "2026-04-03T17:12:00Z",
            "user": {"displayName": "Jira Manager"},
            "issue": {
                "key": "OPS-17",
                "fields": {
                    "assignee": {"displayName": "Primary On-Call"},
                    "status": {"statusCategory": {"key": "done"}},
                },
            },
            "changelog": {"items": [{"field": "assignee"}, {"field": "status"}]},
        },
        headers={"X-Jira-Webhook-Secret": "jira-secret"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json() == {"accepted": True, "processed": 1, "changed": 2, "ignored": 0}

    with Session(dbmod.engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.owner == "Primary On-Call"
        assert incident.status == "resolved"
        assert incident.resolved_at is not None

        notes = session.exec(select(IncidentNote).where(IncidentNote.incident_id == incident_id)).all()
        assert len(notes) == 1
        assert notes[0].author == "jira:Jira Responder"
        assert notes[0].body == "Investigating upstream timeout."

        actions = [
            row.action
            for row in session.exec(
                select(AuditLog).where(AuditLog.target_type == "incident", AuditLog.target_id == incident_id)
            ).all()
        ]
        assert "jira_note" in actions
        assert "jira_assign" in actions
        assert "jira_resolve" in actions


def test_jira_webhook_can_reopen_and_requires_secret(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_webhook_reopen.sqlite'}"
    os.environ["JIRA_WEBHOOK_SECRET"] = "jira-secret"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, CheckType, Incident, Project

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="jira-reopen-project")
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
            jira_issue_key="OPS-99",
            jira_issue_url="https://lastping.atlassian.net/browse/OPS-99",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        incident_id = incident.id

    missing = client.post("/integrations/jira/webhook", json={"issue": {"key": "OPS-99"}})
    assert missing.status_code == 401

    reopen = client.post(
        "/integrations/jira/webhook",
        json={
            "webhookEvent": "jira:issue_updated",
            "timestamp": "2026-04-03T17:20:00Z",
            "user": {"displayName": "Jira Manager"},
            "issue": {"key": "OPS-99", "fields": {"status": {"statusCategory": {"key": "indeterminate"}}}},
            "changelog": {"items": [{"field": "status"}]},
        },
        headers={"X-Jira-Webhook-Secret": "jira-secret"},
    )
    assert reopen.status_code == 200

    with Session(dbmod.engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == "open"
        assert incident.resolved_at is None

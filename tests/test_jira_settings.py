import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_project_jira_settings_roundtrip(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_settings.sqlite'}"
    os.environ["BASE_URL"] = "https://lastping.example"
    os.environ["JIRA_WEBHOOK_SECRET"] = "jira-secret"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="jira-settings-project", api_key_hash=hash_api_key("owner-key"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    get_res = client.get(f"/projects/{project_id}/jira-settings", headers={"X-API-KEY": "owner-key"})
    assert get_res.status_code == 200
    assert get_res.json()["configured"] is False
    assert get_res.json()["inbound_webhook_url"] == "https://lastping.example/integrations/jira/webhook"
    assert get_res.json()["inbound_secret_configured"] is True

    save_res = client.post(
        f"/projects/{project_id}/jira-settings",
        json={
            "base_url": "https://lastping.atlassian.net",
            "user_email": "ops@example.com",
            "api_token": "jira-token",
            "project_key": "ops",
            "issue_type": "Bug",
            "rotation_interval_days": 14,
            "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        },
        headers={"X-API-KEY": "owner-key"},
    )
    assert save_res.status_code == 200
    body = save_res.json()
    assert body["configured"] is True
    assert body["project_key"] == "OPS"
    assert body["issue_type"] == "Bug"
    assert body["api_token_configured"] is True
    assert body["secret_lifecycle"]["rotation_interval_days"] == 14
    assert "api_token" not in body

    get_after_res = client.get(f"/projects/{project_id}/jira-settings", headers={"X-API-KEY": "owner-key"})
    assert get_after_res.status_code == 200
    assert get_after_res.json()["api_token_configured"] is True
    assert "api_token" not in get_after_res.json()

    with Session(dbmod.engine) as session:
        actions = [
            row.action
            for row in session.exec(
                select(AuditLog).where(AuditLog.target_type == "project", AuditLog.target_id == project_id)
            ).all()
        ]
        assert "set_project_jira_settings" in actions


def test_project_jira_settings_clear_token_flow(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_settings_clear.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="jira-clear-project",
            api_key_hash=hash_api_key("owner-key"),
            jira_base_url="https://lastping.atlassian.net",
            jira_user_email="ops@example.com",
            jira_api_token="jira-token",
            jira_project_key="OPS",
            jira_issue_type="Bug",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    clear_res = client.post(
        f"/projects/{project_id}/jira-settings",
        json={"clear_api_token": True},
        headers={"X-API-KEY": "owner-key"},
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["api_token_configured"] is False
    assert clear_res.json()["configured"] is False


def test_project_jira_rotation_grace_falls_back_to_previous_token(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_rotation.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "jira-admin"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, Incident, Project
    from src.notification_queue import process_notification_queue
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="jira-rotation-project",
            api_key_hash=hash_api_key("owner-key"),
            jira_base_url="https://lastping.atlassian.net",
            jira_user_email="ops@example.com",
            jira_api_token="old-jira-token",
            jira_project_key="OPS",
            jira_issue_type="Bug",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="jira-check", type="heartbeat")
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(project_id=project.id, check_id=check.id, status="open")
        session.add(incident)
        session.commit()
        session.refresh(incident)

        project_id = project.id
        incident_id = incident.id

    rotate_res = client.post(
        f"/projects/{project_id}/jira-settings",
        json={"api_token": "new-jira-token", "grace_seconds": 300},
        headers={"X-ADMIN-TOKEN": "jira-admin"},
    )
    assert rotate_res.status_code == 200, rotate_res.text
    assert rotate_res.json()["secret_lifecycle"]["rollover_active_until"] is not None

    attempted_tokens = []

    def fake_create_jira_issue(*, api_token, **kwargs):
        attempted_tokens.append(api_token)
        if api_token == "new-jira-token":
            raise RuntimeError("new token not propagated yet")
        return {"key": "OPS-123", "url": "https://lastping.atlassian.net/browse/OPS-123"}

    monkeypatch.setattr("src.jira.create_jira_issue", fake_create_jira_issue)

    ticket_res = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/jira-ticket",
        headers={"X-ADMIN-TOKEN": "jira-admin"},
    )
    assert ticket_res.status_code == 200, ticket_res.text
    assert ticket_res.json()["queued"] is True

    with Session(dbmod.engine) as session:
        results = process_notification_queue(session)
        assert len(results) == 1
        assert results[0]["ok"] is True

    assert attempted_tokens == ["new-jira-token", "old-jira-token"]

    get_res = client.get(f"/projects/{project_id}/jira-settings", headers={"X-ADMIN-TOKEN": "jira-admin"})
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["secret_lifecycle"]["last_used_at"] is not None
    assert body["secret_lifecycle"]["rollover_active_until"] is not None

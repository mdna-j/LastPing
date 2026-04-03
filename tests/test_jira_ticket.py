import os

from fastapi.testclient import TestClient


def test_create_incident_jira_ticket_and_reuse_link(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_ticket.sqlite'}"
    os.environ["BASE_URL"] = "https://lastping.example"

    from datetime import datetime, timedelta
    from secrets import token_urlsafe
    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Check, CheckType, Incident, Project, ProjectMembership, User, UserToken
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="jira-project",
            api_key_hash=hash_api_key("owner-key"),
            jira_base_url="https://lastping.atlassian.net",
            jira_user_email="ops@example.com",
            jira_api_token="jira-token",
            jira_project_key="OPS",
            jira_issue_type="Task",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, url="https://example.com")
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(project_id=project.id, check_id=check.id, status="open")
        session.add(incident)
        session.commit()
        session.refresh(incident)

        owner = User(email="owner@example.com", hashed_password="x")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        session.add(ProjectMembership(user_id=owner.id, project_id=project.id, role="owner"))
        owner_token = token_urlsafe(16)
        session.add(UserToken(user_id=owner.id, token=owner_token, expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.commit()

        project_id = project.id
        incident_id = incident.id

    calls = []

    def fake_create_jira_issue(**kwargs):
        calls.append(kwargs)
        return {"key": "OPS-17", "url": "https://lastping.atlassian.net/browse/OPS-17"}

    monkeypatch.setattr("src.routers.incidents.create_jira_issue", fake_create_jira_issue)

    res = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/jira-ticket",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] is True
    assert body["issue_key"] == "OPS-17"
    assert body["incident"]["jira_issue_key"] == "OPS-17"
    assert len(calls) == 1
    assert "LastPing incident" in calls[0]["description"]

    second = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/jira-ticket",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert second.status_code == 200
    assert second.json()["created"] is False

    with Session(dbmod.engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident.jira_issue_key == "OPS-17"
        assert incident.jira_issue_url == "https://lastping.atlassian.net/browse/OPS-17"
        actions = [
            row.action
            for row in session.exec(
                select(AuditLog).where(AuditLog.target_type == "incident", AuditLog.target_id == incident_id)
            ).all()
        ]
        assert "create_jira_ticket" in actions

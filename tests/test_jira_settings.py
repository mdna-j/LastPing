import os

from fastapi.testclient import TestClient


def test_project_jira_settings_roundtrip(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_jira_settings.sqlite'}"

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

    save_res = client.post(
        f"/projects/{project_id}/jira-settings",
        json={
            "base_url": "https://lastping.atlassian.net",
            "user_email": "ops@example.com",
            "api_token": "jira-token",
            "project_key": "ops",
            "issue_type": "Bug",
        },
        headers={"X-API-KEY": "owner-key"},
    )
    assert save_res.status_code == 200
    body = save_res.json()
    assert body["configured"] is True
    assert body["project_key"] == "OPS"
    assert body["issue_type"] == "Bug"

    with Session(dbmod.engine) as session:
        actions = [
            row.action
            for row in session.exec(
                select(AuditLog).where(AuditLog.target_type == "project", AuditLog.target_id == project_id)
            ).all()
        ]
        assert "set_project_jira_settings" in actions

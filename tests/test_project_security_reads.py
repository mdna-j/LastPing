import os

from fastapi.testclient import TestClient


def test_public_project_reads_redact_secret_fields(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_project_public_reads.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="public-project",
            api_key_hash=hash_api_key("owner-key"),
            discord_webhook_url="https://discord.example/webhook",
            slack_webhook_url="https://hooks.slack.test/services/one",
            pagerduty_integration_key="pd-secret",
            generic_webhook_url="https://example.com/webhook",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    list_res = client.get("/projects/")
    assert list_res.status_code == 200
    item = next(row for row in list_res.json() if row["id"] == project_id)
    for field in (
        "discord_webhook_url",
        "slack_webhook_url",
        "pagerduty_integration_key",
        "generic_webhook_url",
    ):
        assert field not in item

    get_res = client.get(f"/projects/{project_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    for field in (
        "discord_webhook_url",
        "slack_webhook_url",
        "pagerduty_integration_key",
        "generic_webhook_url",
    ):
        assert field not in body


def test_get_project_webhooks_requires_admin_level_auth(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_project_webhooks_read.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="secure-webhooks-project",
            api_key_hash=hash_api_key("owner-key"),
            discord_webhook_url="https://discord.example/webhook",
            slack_webhook_url="https://hooks.slack.test/services/two",
            pagerduty_integration_key="pd-secret",
            generic_webhook_url="https://example.com/webhook",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    unauth_res = client.get(f"/projects/{project_id}/webhooks")
    assert unauth_res.status_code == 401

    auth_res = client.get(f"/projects/{project_id}/webhooks", headers={"X-API-KEY": "owner-key"})
    assert auth_res.status_code == 200
    body = auth_res.json()
    assert body["discord_webhook_configured"] is True
    assert body["slack_webhook_configured"] is True
    assert body["pagerduty_integration_key_configured"] is True
    assert body["generic_webhook_configured"] is True
    assert body["slack_channel"] is None
    assert "pagerduty_integration_key" not in body


def test_get_project_alert_settings_requires_admin_level_auth(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_project_alert_settings_read.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="secure-alert-settings-project",
            api_key_hash=hash_api_key("owner-key"),
            sms_enabled=True,
            sms_to=None,
            oncall_enabled=True,
            oncall_email="oncall@example.com",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    unauth_res = client.get(f"/projects/{project_id}/alert-settings")
    assert unauth_res.status_code == 401

    auth_res = client.get(f"/projects/{project_id}/alert-settings", headers={"X-API-KEY": "owner-key"})
    assert auth_res.status_code == 200
    body = auth_res.json()
    assert body["sms_to"] is None
    assert body["oncall_email"] == "oncall@example.com"

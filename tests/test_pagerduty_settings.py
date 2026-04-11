import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_project_pagerduty_settings_and_test_delivery(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_settings.sqlite'}"
    os.environ["BASE_URL"] = "https://lastping.example"
    os.environ["PAGERDUTY_WEBHOOK_SECRET"] = "pd-secret"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="pagerduty-settings-project", api_key_hash=hash_api_key("owner-key"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    sent_calls = []

    def fake_send_pagerduty_event(routing_key, summary, severity="critical", **kwargs):
        sent_calls.append(
            {
                "routing_key": routing_key,
                "summary": summary,
                "severity": severity,
                **kwargs,
            }
        )
        return True

    monkeypatch.setattr("src.routers.projects.send_pagerduty_event", fake_send_pagerduty_event)

    get_res = client.get(f"/projects/{project_id}/pagerduty-settings", headers={"X-API-KEY": "owner-key"})
    assert get_res.status_code == 200
    assert get_res.json()["inbound_webhook_url"] == "https://lastping.example/integrations/pagerduty/webhook"
    assert get_res.json()["inbound_secret_configured"] is True

    save_res = client.post(
        f"/projects/{project_id}/pagerduty-settings",
        json={
            "integration_key": "pd-routing-key",
            "rotation_interval_days": 30,
            "expires_at": (datetime.utcnow() + timedelta(days=90)).isoformat(),
        },
        headers={"X-API-KEY": "owner-key"},
    )
    assert save_res.status_code == 200
    assert save_res.json()["integration_key_configured"] is True
    assert save_res.json()["secret_lifecycle"]["rotation_interval_days"] == 30
    assert "integration_key" not in save_res.json()

    get_after_res = client.get(f"/projects/{project_id}/pagerduty-settings", headers={"X-API-KEY": "owner-key"})
    assert get_after_res.status_code == 200
    assert get_after_res.json()["integration_key_configured"] is True
    assert "integration_key" not in get_after_res.json()

    test_res = client.post(
        f"/projects/{project_id}/pagerduty-test",
        headers={"X-API-KEY": "owner-key"},
    )
    assert test_res.status_code == 200
    body = test_res.json()
    assert body["ok"] is True
    assert body["trigger_sent"] is True
    assert body["resolve_sent"] is True
    assert len(sent_calls) == 2
    assert sent_calls[0]["event_action"] == "trigger"
    assert sent_calls[1]["event_action"] == "resolve"
    assert sent_calls[0]["dedup_key"] == sent_calls[1]["dedup_key"]

    with Session(dbmod.engine) as session:
        actions = [
            row.action
            for row in session.exec(
                select(AuditLog).where(AuditLog.target_type == "project", AuditLog.target_id == project_id)
            ).all()
        ]
        assert "set_project_pagerduty_settings" in actions
        assert "send_project_pagerduty_test" in actions


def test_project_pagerduty_settings_clear_flow(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_settings_clear.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="pagerduty-clear-project",
            api_key_hash=hash_api_key("owner-key"),
            pagerduty_integration_key="pd-routing-key",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    clear_res = client.post(
        f"/projects/{project_id}/pagerduty-settings",
        json={"clear_integration_key": True},
        headers={"X-API-KEY": "owner-key"},
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["integration_key_configured"] is False


def test_project_pagerduty_rotation_grace_falls_back_to_previous_key(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_pagerduty_rotation.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="pagerduty-rotation-project",
            api_key_hash=hash_api_key("owner-key"),
            pagerduty_integration_key="old-routing-key",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    rotate_res = client.post(
        f"/projects/{project_id}/pagerduty-settings",
        json={"integration_key": "new-routing-key", "grace_seconds": 300},
        headers={"X-API-KEY": "owner-key"},
    )
    assert rotate_res.status_code == 200, rotate_res.text
    assert rotate_res.json()["secret_lifecycle"]["rollover_active_until"] is not None

    sent_calls = []

    def fake_send_pagerduty_event(routing_key, summary, severity="critical", **kwargs):
        sent_calls.append({"routing_key": routing_key, "summary": summary, "severity": severity, **kwargs})
        return routing_key == "old-routing-key"

    monkeypatch.setattr("src.routers.projects.send_pagerduty_event", fake_send_pagerduty_event)

    test_res = client.post(
        f"/projects/{project_id}/pagerduty-test",
        headers={"X-API-KEY": "owner-key"},
    )
    assert test_res.status_code == 200, test_res.text
    assert [call["routing_key"] for call in sent_calls] == [
        "new-routing-key",
        "old-routing-key",
        "old-routing-key",
    ]

    get_res = client.get(f"/projects/{project_id}/pagerduty-settings", headers={"X-API-KEY": "owner-key"})
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["secret_lifecycle"]["last_used_at"] is not None
    assert body["secret_lifecycle"]["rollover_active_until"] is not None

import os

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
        json={"integration_key": "pd-routing-key"},
        headers={"X-API-KEY": "owner-key"},
    )
    assert save_res.status_code == 200
    assert save_res.json()["integration_key"] == "pd-routing-key"
    assert save_res.json()["integration_key_configured"] is True

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

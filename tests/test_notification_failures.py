import os

from fastapi.testclient import TestClient


def test_notification_failure_list_and_retry_flow(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_notification_failures.sqlite'}"

    from sqlmodel import Session, select
    from src import alerts
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Check, CheckType, Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="retry-project",
            api_key_hash=hash_api_key("owner-key"),
            generic_webhook_url="https://example.com/hook",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="api",
            type=CheckType.HTTP,
            url="https://example.com",
            alert_webhook_enabled=True,
        )
        session.add(check)
        session.commit()
        session.refresh(check)
        project_id = project.id

    attempts = {"count": 0}

    def fake_post_json(url, payload, timeout=10):
        attempts["count"] += 1
        return attempts["count"] > 1

    def fake_post_json_with_response(url, payload, timeout=10, headers=None):
        return {"ok": True, "status": 200}

    monkeypatch.setattr(alerts, "_post_json", fake_post_json)
    monkeypatch.setattr("src.routers.projects.retry_notification_failure_payload", lambda details: {"ok": True, "target": details.get("target"), "response": {"ok": True, "status": 200}})

    with Session(dbmod.engine) as session:
        project = session.get(Project, project_id)
        check = session.exec(select(Check).where(Check.project_id == project_id)).first()
        alerts.notify_degraded(check, project, reason="latency spike")

    list_res = client.get(f"/projects/{project_id}/notification-failures", headers={"X-API-KEY": "owner-key"})
    assert list_res.status_code == 200
    rows = list_res.json()
    assert len(rows) >= 1
    assert rows[0]["channel"] == "webhook"
    assert rows[0]["retryable"] is True
    failure_id = rows[0]["id"]

    retry_res = client.post(f"/projects/{project_id}/notification-failures/{failure_id}/retry", headers={"X-API-KEY": "owner-key"})
    assert retry_res.status_code == 200
    assert retry_res.json()["ok"] is True

    with Session(dbmod.engine) as session:
        retry_logs = session.exec(
            select(AuditLog).where(AuditLog.target_type == "audit_log", AuditLog.target_id == failure_id)
        ).all()
        assert any(row.action == "notification_retry" for row in retry_logs)

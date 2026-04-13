import json
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_notification_failure_list_and_retry_flow(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_notification_failures.sqlite'}"

    from sqlmodel import Session, select
    from src import alerts
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Check, CheckType, NotificationDelivery, Project
    from src.notification_queue import process_notification_queue
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

    def fake_post_json_with_response(url, payload, timeout=10, headers=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"ok": False, "status": 502, "body": "temporary webhook failure"}
        return {"ok": True, "status": 200}

    monkeypatch.setattr(alerts, "_post_json_with_response", fake_post_json_with_response)

    with Session(dbmod.engine) as session:
        project = session.get(Project, project_id)
        check = session.exec(select(Check).where(Check.project_id == project_id)).first()
        alerts.notify_degraded(check, project, reason="latency spike", session=session)
        session.commit()

    with Session(dbmod.engine) as session:
        results = process_notification_queue(session)
        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["delivery_status"] == "retry"

    list_res = client.get(f"/projects/{project_id}/notification-failures", headers={"X-API-KEY": "owner-key"})
    assert list_res.status_code == 200
    rows = list_res.json()
    assert len(rows) >= 1
    assert rows[0]["channel"] == "webhook"
    assert rows[0]["retryable"] is True
    assert rows[0]["delivery_status"] == "retry"
    assert rows[0]["attempt_count"] == 1
    failure_id = rows[0]["id"]

    retry_res = client.post(f"/projects/{project_id}/notification-failures/{failure_id}/retry", headers={"X-API-KEY": "owner-key"})
    assert retry_res.status_code == 200
    assert retry_res.json()["ok"] is True

    with Session(dbmod.engine) as session:
        delivery = session.get(NotificationDelivery, failure_id)
        assert delivery is not None
        assert delivery.status == "delivered"
        retry_logs = session.exec(
            select(AuditLog).where(AuditLog.target_type == "notification_delivery", AuditLog.target_id == failure_id)
        ).all()
        assert any(row.action == "notification_retry" for row in retry_logs)


def test_notification_delivery_ops_detail_cancel_and_poison(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_notification_delivery_ops.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, NotificationDelivery, Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(
            name="delivery-ops-project",
            api_key_hash=hash_api_key("owner-key"),
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        now = datetime.utcnow()
        queued = NotificationDelivery(
            project_id=project.id,
            channel="email",
            event="apikey_rotated",
            request_kind="email",
            target="ops@example.com",
            payload_json=json.dumps({"subject": "Rotation notice", "body": "New API key: lp_secret_value"}),
            status="queued",
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=now,
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
        retrying = NotificationDelivery(
            project_id=project.id,
            channel="webhook",
            event="down",
            request_kind="project_webhook",
            target="project webhook",
            payload_json=json.dumps({"payload": {"title": "check down", "severity": "critical"}}),
            status="retry",
            attempt_count=1,
            max_attempts=5,
            next_attempt_at=now + timedelta(minutes=2),
            last_error="temporary upstream failure",
            created_at=now - timedelta(minutes=8),
            updated_at=now - timedelta(minutes=3),
        )
        session.add(queued)
        session.add(retrying)
        session.commit()
        session.refresh(queued)
        session.refresh(retrying)
        project_id = project.id
        queued_id = queued.id
        retrying_id = retrying.id

    headers = {"X-API-KEY": "owner-key"}
    list_res = client.get(f"/projects/{project_id}/notification-deliveries", headers=headers)
    assert list_res.status_code == 200
    rows = list_res.json()
    assert any(row["id"] == queued_id and row["delivery_status"] == "queued" for row in rows)
    assert any(row["id"] == retrying_id and row["delivery_status"] == "retry" for row in rows)

    detail_res = client.get(f"/projects/{project_id}/notification-deliveries/{queued_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["payload_preview"]["subject"] == "Rotation notice"
    assert detail["payload_preview"]["body"] == "[redacted]"

    cancel_res = client.post(f"/projects/{project_id}/notification-deliveries/{queued_id}/cancel", headers=headers)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["delivery_status"] == "dead"

    poison_res = client.post(f"/projects/{project_id}/notification-deliveries/{retrying_id}/poison", headers=headers)
    assert poison_res.status_code == 200
    assert poison_res.json()["delivery_status"] == "dead"

    with Session(dbmod.engine) as session:
        queued_row = session.get(NotificationDelivery, queued_id)
        retry_row = session.get(NotificationDelivery, retrying_id)
        assert queued_row is not None and queued_row.status == "dead"
        assert retry_row is not None and retry_row.status == "dead"
        audit_rows = session.exec(
            select(AuditLog).where(
                AuditLog.target_type == "notification_delivery",
                AuditLog.target_id.in_([queued_id, retrying_id]),
            )
        ).all()
        actions = {row.action for row in audit_rows}
        assert "notification_cancel" in actions
        assert "notification_poison" in actions

    retry_history_res = client.get(f"/projects/{project_id}/notification-deliveries/{queued_id}", headers=headers)
    assert retry_history_res.status_code == 200
    retry_history = retry_history_res.json()["retry_history"]
    assert any(entry["action"] == "notification_cancel" for entry in retry_history)

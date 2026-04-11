import json
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_login_and_api_key_failures_are_audited(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_security_ops_auth.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog, Project, User
    from src.security import hash_api_key, hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        session.add(User(email="ops@example.com", hashed_password=hash_password("CorrectHorse1")))
        project = Project(name="secure-project", api_key_hash=hash_api_key("real-key"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    bad_login = client.post("/users/login", json={"email": "ops@example.com", "password": "wrong-pass"})
    assert bad_login.status_code == 401
    assert bad_login.json()["detail"] == "Invalid credentials"

    bad_api_key = client.get(f"/projects/{project_id}/webhooks", headers={"X-API-KEY": "bad-key"})
    assert bad_api_key.status_code == 403
    assert bad_api_key.json()["detail"] == "Invalid API key"

    with Session(dbmod.engine) as session:
        rows = session.exec(select(AuditLog).order_by(AuditLog.created_at.asc())).all()
        actions = [row.action for row in rows]
        assert "auth_invalid_credentials" in actions
        assert "auth_invalid_api_key" in actions

        invalid_api_row = next(row for row in rows if row.action == "auth_invalid_api_key")
        details = json.loads(invalid_api_row.details)
        assert details["path"] == f"/projects/{project_id}/webhooks"
        assert invalid_api_row.project_id == project_id


def test_invalid_signed_webhook_is_audited(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_security_ops_webhook.sqlite'}"
    os.environ["JIRA_WEBHOOK_SECRET"] = "jira-secret"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog

    dbmod.create_db_and_tables()
    client = TestClient(app)

    response = client.post(
        "/integrations/jira/webhook",
        json={"issue": {"key": "OPS-1"}},
        headers={
            "X-Jira-Webhook-Timestamp": "2026-04-11T12:00:00Z",
            "X-Jira-Webhook-Signature": "00" * 32,
        },
    )
    assert response.status_code == 403
    assert "Invalid webhook signature" in response.json()["detail"]

    with Session(dbmod.engine) as session:
        row = session.exec(
            select(AuditLog).where(AuditLog.action == "webhook_invalid_signature").order_by(AuditLog.created_at.desc())
        ).first()
        assert row is not None
        details = json.loads(row.details)
        assert details["path"] == "/integrations/jira/webhook"


def test_security_ops_summary_groups_security_events(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_security_ops_summary.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "security-admin"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import AuditLog

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        now = datetime.utcnow()
        session.add(
            AuditLog(
                actor="admin",
                action="rotate_primary_api_key",
                target_type="project",
                target_id=1,
                project_id=1,
                created_at=now - timedelta(minutes=5),
            )
        )
        session.add(
            AuditLog(
                actor="admin",
                action="create_scoped_project_token",
                target_type="project",
                target_id=1,
                project_id=1,
                created_at=now - timedelta(minutes=4),
            )
        )
        session.add(
            AuditLog(
                actor="security",
                action="notification_failed",
                target_type="project",
                target_id=1,
                project_id=1,
                details=json.dumps({"channel": "slack", "target": "#ops"}),
                created_at=now - timedelta(minutes=3),
            )
        )
        session.add(
            AuditLog(
                actor="security",
                action="auth_invalid_token",
                target_type="auth",
                actor_ip="203.0.113.7",
                details=json.dumps({"path": "/ui/dashboard"}),
                created_at=now - timedelta(minutes=2),
            )
        )
        session.add(
            AuditLog(
                actor="security",
                action="auth_invalid_api_key",
                target_type="auth",
                actor_ip="203.0.113.7",
                details=json.dumps({"path": "/projects/1/webhooks"}),
                project_id=1,
                created_at=now - timedelta(minutes=1),
            )
        )
        session.commit()

    response = client.get("/admin/security/ops/summary?hours=24", headers={"X-ADMIN-TOKEN": "security-admin"})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["secret_changes"] == 1
    assert body["counts"]["token_events"] == 1
    assert body["counts"]["webhook_failures"] == 1
    assert body["counts"]["admin_actions"] >= 2
    assert body["counts"]["suspicious_auth_events"] == 2
    assert body["counts"]["suspicious_auth_patterns"] == 1
    assert body["suspicious_auth_patterns"][0]["actor_ip"] == "203.0.113.7"
    assert body["suspicious_auth_patterns"][0]["count"] == 2


def test_security_ops_ui_page_loads_without_admin_header(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_security_ops_ui.sqlite'}"

    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    response = client.get("/admin/security/ui")
    assert response.status_code == 200
    assert "Security Ops" in response.text

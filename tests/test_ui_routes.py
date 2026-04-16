import os
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_main_ui_pages_render_expected_shell_and_auth_inputs(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_ui_routes.sqlite'}"

    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    pages = [
        ("/ui/dashboard", "Project Dashboard", 'id="apiKey"'),
        ("/ui/snapshots", "Snapshots", 'id="apiKey"'),
        ("/ui/reports", "Availability Reports", 'id="apiKey"'),
        ("/ui/incidents", "Incidents", 'id="userToken"'),
        ("/ui/projects/1/settings", "Project Settings", 'id="adminToken"'),
        ("/ui/projects/1/oncall", "On-call", 'id="adminToken"'),
    ]

    for path, heading, marker in pages:
        resp = client.get(path)
        assert resp.status_code == 200
        assert heading in resp.text
        assert marker in resp.text
        assert "health-strip" in resp.text
        if path == "/ui/projects/1/settings":
            assert 'id="deliveryStatusFilter"' in resp.text
            assert 'id="deliveryInspectPanel"' in resp.text

    account_resp = client.get("/ui/account")
    assert account_resp.status_code == 200
    assert "Enterprise Access" in account_resp.text
    assert 'id="authEmail"' in account_resp.text
    assert 'id="sessionRows"' in account_resp.text


def test_dashboard_health_returns_expected_summary_fields(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_ui_health.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import (
        AuditLog,
        Check,
        CheckLease,
        CheckStatus,
        CheckType,
        Incident,
        NotificationDelivery,
        OnCallAlert,
        PredictiveModel,
        PredictiveModelQuality,
        Project,
        RemediationApproval,
        RemediationHook,
    )
    from src.runtime_metrics import reset_request_metrics

    dbmod.create_db_and_tables()
    reset_request_metrics()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="ui-health-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        down_check = Check(
            project_id=project.id,
            name="down-check",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.DOWN,
            region="us-east-1",
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(minutes=15),
        )
        up_check = Check(
            project_id=project.id,
            name="up-check",
            type=CheckType.HTTP,
            status=CheckStatus.UP,
            region="us-west-2",
            url="https://example.com/health",
            interval=60,
            next_run=datetime.utcnow() - timedelta(minutes=9),
        )
        session.add(down_check)
        session.add(up_check)
        session.commit()
        session.refresh(down_check)
        session.refresh(up_check)

        incident = Incident(
            project_id=project.id,
            check_id=down_check.id,
            started_at=datetime.utcnow() - timedelta(minutes=7),
            status="open",
        )
        session.add(incident)
        session.add(
            CheckLease(
                check_id=down_check.id,
                lease_owner="worker-us-east-1",
                lease_expires_at=datetime.utcnow() + timedelta(minutes=2),
                updated_at=datetime.utcnow(),
                lease_fence=1,
            )
        )
        hook = RemediationHook(
            project_id=project.id,
            check_id=down_check.id,
            event_type="down",
            url="https://example.com/remediate",
        )
        session.add(hook)
        session.commit()
        session.refresh(hook)

        session.add(
            OnCallAlert(
                project_id=project.id,
                check_id=down_check.id,
                event_type="down",
                status="open",
                created_at=datetime.utcnow() - timedelta(minutes=20),
            )
        )
        session.add(
            RemediationApproval(
                hook_id=hook.id,
                project_id=project.id,
                check_id=down_check.id,
                event_type="down",
                status="pending",
                requested_at=datetime.utcnow() - timedelta(minutes=12),
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            )
        )
        session.add(
            AuditLog(
                actor="worker",
                action="raw_retention_pruned",
                target_type="system",
                details=json.dumps(
                    {
                        "truncated_tables": ["events"],
                        "ran_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
                    }
                ),
                created_at=datetime.utcnow() - timedelta(hours=2),
            )
        )
        session.add(
            AuditLog(
                actor="alerts",
                action="notification_failed",
                target_type="project",
                target_id=project.id,
                details=json.dumps({"channel": "slack", "event": "down"}),
                created_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        session.add(
            NotificationDelivery(
                project_id=project.id,
                check_id=down_check.id,
                channel="slack",
                event="down",
                request_kind="slack",
                target="#ops",
                payload_json="{}",
                status="queued",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at=datetime.utcnow() - timedelta(minutes=1),
                created_at=datetime.utcnow() - timedelta(minutes=20),
                updated_at=datetime.utcnow() - timedelta(minutes=20),
            )
        )
        session.add(
            NotificationDelivery(
                project_id=project.id,
                check_id=down_check.id,
                channel="pagerduty",
                event="down",
                request_kind="pagerduty",
                target="service-key",
                payload_json="{}",
                status="retry",
                attempt_count=2,
                max_attempts=5,
                next_attempt_at=datetime.utcnow() - timedelta(seconds=30),
                created_at=datetime.utcnow() - timedelta(minutes=15),
                updated_at=datetime.utcnow() - timedelta(minutes=2),
            )
        )
        session.add(
            NotificationDelivery(
                project_id=project.id,
                check_id=down_check.id,
                channel="email",
                event="down",
                request_kind="email",
                target="ops@example.com",
                payload_json="{}",
                status="processing",
                attempt_count=1,
                max_attempts=5,
                next_attempt_at=datetime.utcnow() - timedelta(minutes=1),
                claimed_at=datetime.utcnow() - timedelta(seconds=40),
                created_at=datetime.utcnow() - timedelta(minutes=5),
                updated_at=datetime.utcnow() - timedelta(seconds=40),
            )
        )
        session.add(
            NotificationDelivery(
                project_id=project.id,
                check_id=down_check.id,
                channel="slack",
                event="down",
                request_kind="slack",
                target="#ops",
                payload_json="{}",
                status="delivered",
                attempt_count=1,
                max_attempts=5,
                next_attempt_at=datetime.utcnow() - timedelta(minutes=9),
                delivered_at=datetime.utcnow() - timedelta(minutes=8),
                created_at=datetime.utcnow() - timedelta(minutes=10),
                updated_at=datetime.utcnow() - timedelta(minutes=8),
            )
        )
        session.add(
            NotificationDelivery(
                project_id=project.id,
                check_id=down_check.id,
                channel="pagerduty",
                event="down",
                request_kind="pagerduty",
                target="service-key",
                payload_json="{}",
                status="dead",
                attempt_count=3,
                max_attempts=5,
                next_attempt_at=datetime.utcnow() - timedelta(minutes=2),
                dead_at=datetime.utcnow() - timedelta(minutes=1),
                created_at=datetime.utcnow() - timedelta(minutes=12),
                updated_at=datetime.utcnow() - timedelta(minutes=1),
            )
        )
        model = PredictiveModel(
            project_id=project.id,
            check_id=down_check.id,
            active=True,
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        session.add(
            PredictiveModelQuality(
                predictive_model_id=model.id,
                project_id=project.id,
                check_id=down_check.id,
                window_start=datetime.utcnow() - timedelta(days=2),
                window_end=datetime.utcnow() - timedelta(days=1),
                sample_count=42,
                drift_ratio=2.4,
                status="drift",
                created_at=datetime.utcnow() - timedelta(hours=6),
            )
        )
        session.commit()

        project_id = project.id
        down_check_id = down_check.id

    health_resp = client.get("/health")
    assert health_resp.status_code == 200

    resp = client.get(f"/ui/dashboard/health?project_id={project_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == project_id
    assert body["active_incidents"] == 1
    assert body["workers_online"] == 1
    assert body["down_checks_count"] == 1
    assert body["primary_down_check"]["id"] == down_check_id
    assert "us-east-1: 1 down" in body["region_health_summary"]
    assert any(region["name"] == "us-west-2" and region["up"] == 1 for region in body["regions"])
    assert body["platform"]["worker_lag"]["overdue_checks"] == 1
    assert body["platform"]["queue_health"]["open_oncall_alerts"] == 1
    assert body["platform"]["queue_health"]["pending_approvals"] == 1
    assert body["platform"]["notification_queue"]["depth"] == 3
    assert body["platform"]["notification_queue"]["retrying"] == 1
    assert body["platform"]["notification_queue"]["dead_letters"] == 1
    assert body["platform"]["notification_queue"]["per_channel_success"]["slack"]["success_rate"] == 1.0
    assert body["platform"]["notification_queue"]["per_channel_success"]["pagerduty"]["success_rate"] == 0.0
    assert body["platform"]["retention"]["truncated_tables"] == ["events"]
    assert body["platform"]["failed_notifications"]["failures_24h"] == 1
    assert body["platform"]["model_ops"]["drifted_models"] == 1
    assert body["platform"]["api_latency"]["request_count"] >= 1

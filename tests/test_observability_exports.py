import json
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_prometheus_export_requires_admin_and_exposes_platform_metrics(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_observability_prom.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "observability-admin"
    os.environ["OTEL_SERVICE_NAME"] = "lastping-test"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import (
        AuditLog,
        Check,
        CheckStatus,
        CheckType,
        OnCallAlert,
        PredictiveModel,
        PredictiveModelQuality,
        Project,
        RemediationApproval,
        RemediationHook,
    )

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="obs-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="api-check",
            type=CheckType.HTTP,
            status=CheckStatus.UP,
            url="https://example.com/health",
            region="us-east-1",
            interval=60,
            next_run=datetime.utcnow() - timedelta(minutes=7),
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        hook = RemediationHook(
            project_id=project.id,
            check_id=check.id,
            event_type="down",
            url="https://example.com/remediate",
        )
        session.add(hook)
        session.commit()
        session.refresh(hook)

        session.add(
            OnCallAlert(
                project_id=project.id,
                check_id=check.id,
                event_type="down",
                status="open",
                created_at=datetime.utcnow() - timedelta(minutes=10),
            )
        )
        session.add(
            RemediationApproval(
                hook_id=hook.id,
                project_id=project.id,
                check_id=check.id,
                event_type="down",
                status="pending",
                requested_at=datetime.utcnow() - timedelta(minutes=8),
                expires_at=datetime.utcnow() + timedelta(minutes=15),
            )
        )
        session.add(
            AuditLog(
                actor="worker",
                action="raw_retention_pruned",
                target_type="system",
                details=json.dumps({"truncated_tables": ["events"], "ran_at": datetime.utcnow().isoformat()}),
                created_at=datetime.utcnow() - timedelta(hours=3),
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
                project_id=project.id,
            )
        )
        model = PredictiveModel(project_id=project.id, check_id=check.id, active=True)
        session.add(model)
        session.commit()
        session.refresh(model)
        session.add(
            PredictiveModelQuality(
                predictive_model_id=model.id,
                project_id=project.id,
                check_id=check.id,
                window_start=datetime.utcnow() - timedelta(days=2),
                window_end=datetime.utcnow() - timedelta(days=1),
                sample_count=12,
                drift_ratio=2.1,
                status="drift",
                created_at=datetime.utcnow() - timedelta(hours=2),
            )
        )
        session.commit()
        project_id = project.id

    health_resp = client.get("/health")
    assert health_resp.status_code == 200

    unauthorized = client.get(f"/observability/prometheus?project_id={project_id}")
    assert unauthorized.status_code == 401

    resp = client.get(
        f"/observability/prometheus?project_id={project_id}",
        headers={"X-ADMIN-TOKEN": "observability-admin"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "lastping_runtime_info{service=\"lastping-test\"} 1" in resp.text
    assert "lastping_api_requests_window_total" in resp.text
    assert "lastping_project_worker_overdue_checks" in resp.text
    assert "lastping_project_queue_pending_approvals" in resp.text
    assert "lastping_project_retention_truncated_tables" in resp.text
    assert "lastping_project_failed_notifications_channel_24h" in resp.text
    assert "lastping_project_model_drifted_models" in resp.text
    assert f'project_id="{project_id}"' in resp.text


def test_otel_trace_export_captures_traceparent_and_response_headers(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_observability_traces.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "observability-admin"

    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    incoming_traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    resp = client.get("/health", headers={"traceparent": incoming_traceparent})
    assert resp.status_code == 200
    assert "traceparent" in resp.headers
    assert "X-Trace-Id" in resp.headers

    returned_traceparent = resp.headers["traceparent"]
    parts = returned_traceparent.split("-")
    assert len(parts) == 4
    assert parts[1] == "0123456789abcdef0123456789abcdef"
    assert parts[2] != "0123456789abcdef"
    assert resp.headers["X-Trace-Id"] == parts[1]

    export = client.get("/observability/otel/traces", headers={"X-ADMIN-TOKEN": "observability-admin"})
    assert export.status_code == 200
    body = export.json()
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert body["trace_count"] >= 1
    assert any(
        span["traceId"] == "0123456789abcdef0123456789abcdef"
        and span["parentSpanId"] == "0123456789abcdef"
        and span["name"] == "GET /health"
        for span in spans
    )

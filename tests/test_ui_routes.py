import os
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


def test_dashboard_health_returns_expected_summary_fields(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_ui_health.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus, Incident, CheckLease

    dbmod.create_db_and_tables()
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
        session.commit()

        project_id = project.id
        down_check_id = down_check.id

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

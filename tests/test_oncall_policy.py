import os

from fastapi.testclient import TestClient


def test_oncall_preview_filters_event_types(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_preview.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus, OnCallEscalation
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="oncallproj", api_key_hash=hash_api_key("oncallkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        esc_down = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=0,
            delay_minutes=5,
            target_type="email",
            target_value="down@example.com",
            event_types="down",
        )
        esc_degraded = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=0,
            delay_minutes=5,
            target_type="sms",
            target_value="+15551230000",
            event_types="degraded",
        )
        esc_any = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=1,
            delay_minutes=10,
            target_type="email",
            target_value="any@example.com",
            event_types=None,
        )
        session.add_all([esc_down, esc_degraded, esc_any])
        session.commit()
        project_id = project.id

    client = TestClient(app)
    headers = {"X-API-KEY": "oncallkey"}
    resp = client.get(f"/projects/{project_id}/oncall/escalations/preview?event_type=down", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    steps = data.get("steps", [])
    assert steps
    step0_targets = [c.get("target_value") for c in steps[0].get("channels", [])]
    assert "down@example.com" in step0_targets
    assert "+15551230000" not in step0_targets


def test_oncall_apply_template(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_template.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus, OnCallEscalation
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="tmplproj", api_key_hash=hash_api_key("tmplkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="web", type=CheckType.HTTP, status=CheckStatus.UP)
        session.add(check)
        session.commit()
        session.refresh(check)

        esc1 = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=0,
            delay_minutes=5,
            target_type="email",
            target_value="a@example.com",
            event_types="down",
        )
        esc2 = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=1,
            delay_minutes=10,
            target_type="sms",
            target_value="+15550001111",
            event_types="down,degraded",
        )
        session.add_all([esc1, esc2])
        session.commit()
        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}
    resp = client.post(
        f"/projects/{project_id}/oncall/escalations/apply-template",
        json={"source_check_id": None, "target_check_id": check_id, "overwrite": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200

    headers = {"X-API-KEY": "tmplkey"}
    list_resp = client.get(f"/projects/{project_id}/oncall/escalations?check_id={check_id}", headers=headers)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 2
    assert all(r["check_id"] == check_id for r in rows)


def test_oncall_patch_check_routing_allows_null_clears(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_routing.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus, OnCallEscalation
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="routeproj", api_key_hash=hash_api_key("routekey"), oncall_enabled=True, oncall_email="proj@example.com")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="api",
            type=CheckType.HTTP,
            status=CheckStatus.UP,
            alert_slack_webhook_url="https://example.com/slack",
            escalation_after_minutes=10,
            escalation_cooldown_seconds=3600,
        )
        session.add(check)
        session.commit()
        session.refresh(check)
        esc = OnCallEscalation(
            project_id=project.id,
            check_id=None,
            level=0,
            delay_minutes=5,
            target_type="email",
            target_value="ops@example.com",
            enabled=True,
        )
        session.add(esc)
        session.commit()
        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}

    # Update a subset of fields; omitted fields should remain unchanged.
    resp = client.patch(
        f"/projects/{project_id}/oncall/checks/{check_id}/routing",
        json={
            "alert_oncall_enabled": True,
            "alert_oncall_email": "check@example.com",
            "alert_sms_enabled": False,
            "alert_sms_to": None,  # explicit null clears override
            "escalation_after_minutes": None,  # explicit null disables per-check escalation timer
            "escalation_cooldown_seconds": 120,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    with Session(dbmod.engine) as session:
        chk = session.get(Check, check_id)
        assert chk is not None
        assert chk.alert_oncall_enabled is True
        assert chk.alert_oncall_email == "check@example.com"
        assert chk.alert_sms_enabled is False
        assert chk.alert_sms_to is None
        assert chk.escalation_after_minutes is None
        assert chk.escalation_cooldown_seconds == 120
        # unchanged because omitted
        assert chk.alert_slack_webhook_url == "https://example.com/slack"

    # Now clear slack override explicitly.
    resp2 = client.patch(
        f"/projects/{project_id}/oncall/checks/{check_id}/routing",
        json={"alert_slack_webhook_url": None},
        headers=admin_headers,
    )
    assert resp2.status_code == 200, resp2.text

    with Session(dbmod.engine) as session:
        chk = session.get(Check, check_id)
        assert chk.alert_slack_webhook_url is None


def test_oncall_create_escalation_rejects_invalid_email_target(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_invalid_target.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="invalidtarget", api_key_hash=hash_api_key("targetkey"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}
    resp = client.post(
        f"/projects/{project_id}/oncall/escalations",
        json={
            "level": 0,
            "delay_minutes": 5,
            "target_type": "email",
            "target_value": "not-an-email",
            "enabled": True,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "valid email" in resp.text


def test_oncall_patch_routing_rejects_enabled_channel_without_destination(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_invalid_routing.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="routingproj", api_key_hash=hash_api_key("routingkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, status=CheckStatus.UP)
        session.add(check)
        session.commit()
        session.refresh(check)
        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}
    resp = client.patch(
        f"/projects/{project_id}/oncall/checks/{check_id}/routing",
        json={"alert_slack_enabled": True},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "Slack alerts are enabled" in resp.text


def test_oncall_patch_routing_accepts_slack_channel_without_webhook(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_slack_channel.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, CheckStatus, CheckType, Project

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="routingproj-channel")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, status=CheckStatus.UP)
        session.add(check)
        session.commit()
        session.refresh(check)
        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}
    resp = client.patch(
        f"/projects/{project_id}/oncall/checks/{check_id}/routing",
        json={"alert_slack_enabled": True, "alert_slack_channel": "COPS"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    with Session(dbmod.engine) as session:
        chk = session.get(Check, check_id)
        assert chk.alert_slack_enabled is True
        assert chk.alert_slack_channel == "COPS"


def test_oncall_patch_routing_rejects_oncall_enable_without_escalation_steps(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'oncall_no_steps.db'}"
    os.environ["ADMIN_TOKEN"] = "adminkey"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(
            name="nostepsproj",
            api_key_hash=hash_api_key("nostepskey"),
            oncall_enabled=True,
            oncall_email="ops@example.com",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, status=CheckStatus.UP)
        session.add(check)
        session.commit()
        session.refresh(check)
        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    admin_headers = {"X-ADMIN-TOKEN": "adminkey"}
    resp = client.patch(
        f"/projects/{project_id}/oncall/checks/{check_id}/routing",
        json={"alert_oncall_enabled": True},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "no enabled escalation steps" in resp.text

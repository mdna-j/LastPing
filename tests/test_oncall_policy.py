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
    headers = {"Authorization": "Bearer oncallkey"}
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

    headers = {"Authorization": "Bearer tmplkey"}
    list_resp = client.get(f"/projects/{project_id}/oncall/escalations?check_id={check_id}", headers=headers)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 2
    assert all(r["check_id"] == check_id for r in rows)

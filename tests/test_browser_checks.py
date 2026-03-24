import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_create_browser_check_with_steps(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_api.sqlite'}"

    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    create_project = client.post("/projects/", json={"name": "browser-proj"})
    assert create_project.status_code == 201, create_project.text
    project_payload = create_project.json()
    project_id = project_payload["project"]["id"]
    api_key = project_payload["api_key"]

    check_payload = {
        "name": "login-flow",
        "type": "browser",
        "url": "https://example.com/login",
        "interval": 120,
        "browser_steps": [
            {"action": "fill", "selector": "#email", "value": "${LASTPING_BROWSER_USER}"},
            {"action": "fill", "selector": "#password", "value": "${LASTPING_BROWSER_PASSWORD}"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "expect_url", "value": "https://example.com/dashboard"},
        ],
        "browser_capture_screenshot": True,
    }
    create_check = client.post(
        f"/projects/{project_id}/checks/",
        json=check_payload,
        headers={"X-API-KEY": api_key},
    )
    assert create_check.status_code == 201, create_check.text
    created = create_check.json()
    assert created["type"] == "browser"
    assert created["browser_capture_screenshot"] is True
    assert len(created["browser_steps"]) == 4
    assert created["browser_steps"][0]["action"] == "fill"


def test_worker_dispatches_browser_checks(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_worker.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.models import Check, CheckResult, CheckStatus, CheckType, Project
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="browser-worker")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="checkout-flow",
            type=CheckType.BROWSER,
            url="https://example.com/checkout",
            browser_steps='[{"action":"click","selector":"#buy"}]',
            browser_capture_screenshot=True,
            interval=60,
            next_run=datetime.utcnow() - timedelta(seconds=1),
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        monkeypatch.setattr(worker, "_browser_check", lambda chk, proj, timeout, retries: (True, "browser_ok", 222.4))

        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.status == CheckStatus.UP
        assert check.last_latency_ms == 222.4
        latest_result = session.exec(
            select(CheckResult).where(CheckResult.check_id == check.id).order_by(CheckResult.id.desc())
        ).first()
        assert latest_result is not None
        assert latest_result.status == CheckStatus.UP
        assert latest_result.latency_ms == 222.4


def test_browser_template_requires_prefixed_env_vars():
    from src import worker

    os.environ["LASTPING_BROWSER_USER"] = "alice@example.com"
    assert worker._expand_browser_template("user=${LASTPING_BROWSER_USER}") == "user=alice@example.com"

    try:
        worker._expand_browser_template("${SECRET_TOKEN}")
    except ValueError as exc:
        assert "browser_env_prefix_required" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-prefixed browser env var")

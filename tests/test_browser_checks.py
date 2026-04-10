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


def test_worker_persists_browser_artifacts_and_links_incident(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_artifacts.sqlite'}"
    os.environ["BROWSER_CHECK_ARTIFACT_DIR"] = str(tmp_path / "browser_artifacts")

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.models import BrowserCheckArtifact, Check, CheckResult, CheckStatus, CheckType, Incident, Project
    from src import worker

    dbmod.create_db_and_tables()

    artifact_root = tmp_path / "browser_artifacts" / "project-1" / "check-1" / "run-1"
    artifact_root.mkdir(parents=True, exist_ok=True)
    screenshot = artifact_root / "failure.png"
    screenshot.write_bytes(b"png-bytes")
    video = artifact_root / "session.webm"
    video.write_bytes(b"webm-bytes")
    har = artifact_root / "network.har"
    har.write_text("{}", encoding="utf-8")

    with Session(dbmod.engine) as session:
        project = Project(name="browser-artifacts")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="browser-failure",
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

        monkeypatch.setattr(
            worker,
            "_browser_check",
            lambda chk, proj, timeout, retries: (
                False,
                "browser_step_failed:0:click:nope",
                None,
                [
                    {
                        "artifact_type": "screenshot",
                        "file_path": str(screenshot.resolve()),
                        "content_type": "image/png",
                        "size_bytes": screenshot.stat().st_size,
                    },
                    {
                        "artifact_type": "video",
                        "file_path": str(video.resolve()),
                        "content_type": "video/webm",
                        "size_bytes": video.stat().st_size,
                    },
                    {
                        "artifact_type": "har",
                        "file_path": str(har.resolve()),
                        "content_type": "application/json",
                        "size_bytes": har.stat().st_size,
                    },
                ],
            ),
        )

        worker.scan_checks_once(session)

        incident = session.exec(select(Incident).where(Incident.check_id == check.id)).first()
        assert incident is not None
        result = session.exec(
            select(CheckResult).where(CheckResult.check_id == check.id).order_by(CheckResult.id.desc())
        ).first()
        assert result is not None
        assert result.incident_id == incident.id
        artifacts = session.exec(
            select(BrowserCheckArtifact).where(BrowserCheckArtifact.check_id == check.id).order_by(BrowserCheckArtifact.id)
        ).all()
        assert [artifact.artifact_type for artifact in artifacts] == ["screenshot", "video", "har"]
        assert all(artifact.incident_id == incident.id for artifact in artifacts)
        assert all(artifact.check_result_id == result.id for artifact in artifacts)


def test_incident_detail_includes_browser_artifacts_and_downloads(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_incident.sqlite'}"
    os.environ["BROWSER_CHECK_ARTIFACT_DIR"] = str(tmp_path / "browser_incident_artifacts")

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import BrowserCheckArtifact, Check, CheckResult, CheckStatus, CheckType, Incident

    dbmod.create_db_and_tables()
    client = TestClient(app)

    create_project = client.post("/projects/", json={"name": "browser-incident"})
    assert create_project.status_code == 201, create_project.text
    project_payload = create_project.json()
    project_id = project_payload["project"]["id"]
    api_key = project_payload["api_key"]

    artifact_root = tmp_path / "browser_incident_artifacts" / f"project-{project_id}" / "check-1" / "run-1"
    artifact_root.mkdir(parents=True, exist_ok=True)
    screenshot = artifact_root / "failure.png"
    screenshot.write_bytes(b"png")

    with Session(dbmod.engine) as session:
        check = Check(
            project_id=project_id,
            name="browser-incident-check",
            type=CheckType.BROWSER,
            url="https://example.com",
            browser_steps='[{"action":"goto","value":"https://example.com"}]',
            status=CheckStatus.DOWN,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(project_id=project_id, check_id=check.id, status="open")
        session.add(incident)
        session.commit()
        session.refresh(incident)

        result = CheckResult(
            project_id=project_id,
            check_id=check.id,
            incident_id=incident.id,
            status=CheckStatus.DOWN,
            error_message="browser_step_failed",
        )
        session.add(result)
        session.commit()
        session.refresh(result)

        artifact = BrowserCheckArtifact(
            project_id=project_id,
            check_id=check.id,
            check_result_id=result.id,
            incident_id=incident.id,
            artifact_type="screenshot",
            file_path=str(screenshot.resolve()),
            content_type="image/png",
            size_bytes=screenshot.stat().st_size,
        )
        session.add(artifact)
        session.commit()
        session.refresh(artifact)

        incident_id = incident.id
        artifact_id = artifact.id

    detail = client.get(
        f"/projects/{project_id}/incidents/{incident_id}",
        headers={"X-API-KEY": api_key},
    )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert len(payload["artifacts"]) == 1
    assert payload["artifacts"][0]["artifact_type"] == "screenshot"
    assert payload["artifacts"][0]["download_url"].endswith(f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact_id}")

    download = client.get(
        f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact_id}",
        headers={"X-API-KEY": api_key},
    )
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("image/png")
    assert download.content == b"png"


def test_browser_artifact_retention_prunes_files_and_rows(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_retention.sqlite'}"
    os.environ["BROWSER_CHECK_ARTIFACT_DIR"] = str(tmp_path / "browser_retention_artifacts")

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.models import BrowserCheckArtifact, Check, CheckType, Project
    from src import worker

    dbmod.create_db_and_tables()
    worker._LAST_BROWSER_ARTIFACT_RETENTION_RUN = None

    stale_root = tmp_path / "browser_retention_artifacts" / "project-1" / "check-1" / "stale"
    stale_root.mkdir(parents=True, exist_ok=True)
    stale_file = stale_root / "failure.png"
    stale_file.write_bytes(b"old")

    fresh_root = tmp_path / "browser_retention_artifacts" / "project-1" / "check-1" / "fresh"
    fresh_root.mkdir(parents=True, exist_ok=True)
    fresh_file = fresh_root / "failure.png"
    fresh_file.write_bytes(b"new")

    with Session(dbmod.engine) as session:
        project = Project(name="browser-retention")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="browser-check", type=CheckType.BROWSER, url="https://example.com")
        session.add(check)
        session.commit()
        session.refresh(check)

        session.add(
            BrowserCheckArtifact(
                project_id=project.id,
                check_id=check.id,
                artifact_type="screenshot",
                file_path=str(stale_file.resolve()),
                content_type="image/png",
                size_bytes=stale_file.stat().st_size,
                created_at=datetime.utcnow() - timedelta(days=30),
            )
        )
        session.add(
            BrowserCheckArtifact(
                project_id=project.id,
                check_id=check.id,
                artifact_type="screenshot",
                file_path=str(fresh_file.resolve()),
                content_type="image/png",
                size_bytes=fresh_file.stat().st_size,
                created_at=datetime.utcnow(),
            )
        )
        session.commit()

        worker._maybe_prune_browser_artifacts(session, datetime.utcnow())

        remaining = session.exec(select(BrowserCheckArtifact).order_by(BrowserCheckArtifact.id)).all()
        assert len(remaining) == 1
        assert remaining[0].file_path == str(fresh_file.resolve())
        assert not stale_file.exists()
        assert fresh_file.exists()

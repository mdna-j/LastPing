import os
import json
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
            {"action": "fill", "selector": "#email", "value": "${browser_secret:login_email}"},
            {"action": "fill", "selector": "#password", "value": "${browser_secret:login_password}"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "expect_visible", "selector": "[data-test=dashboard]"},
            {"action": "expect_attribute", "selector": "body", "attribute": "data-auth", "value": "ready"},
            {"action": "expect_count", "selector": ".toast", "count": 1},
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
    assert len(created["browser_steps"]) == 7
    assert created["browser_steps"][0]["action"] == "fill"
    assert created["browser_steps"][3]["action"] == "expect_visible"
    assert created["browser_steps"][4]["attribute"] == "data-auth"
    assert created["browser_steps"][5]["count"] == 1


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


def test_browser_secret_crud_and_template_resolution(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_secrets.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "browser-admin"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src import worker
    from src.models import BrowserCheckSecret

    dbmod.create_db_and_tables()
    client = TestClient(app)

    create_project = client.post("/projects/", json={"name": "browser-secrets"})
    assert create_project.status_code == 201, create_project.text
    project_id = create_project.json()["project"]["id"]
    admin_headers = {"X-ADMIN-TOKEN": "browser-admin"}

    upsert = client.post(
        f"/projects/{project_id}/browser-secrets",
        json={
            "name": "login_password",
            "value": "super-secret-password",
            "description": "Checkout login password",
        },
        headers=admin_headers,
    )
    assert upsert.status_code == 200, upsert.text
    payload = upsert.json()
    assert payload["name"] == "login_password"
    assert payload["configured"] is True
    assert "value" not in payload

    listed = client.get(f"/projects/{project_id}/browser-secrets", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert len(body) == 1
    assert body[0]["name"] == "login_password"
    assert body[0]["configured"] is True
    assert "value" not in body[0]

    expanded = worker._expand_browser_template_with_secrets(
        "pw=${browser_secret:login_password}",
        project_id=project_id,
        secret_cache={},
    )
    assert expanded == "pw=super-secret-password"

    with Session(dbmod.engine) as session:
        secret = session.exec(
            select(BrowserCheckSecret).where(
                BrowserCheckSecret.project_id == project_id,
                BrowserCheckSecret.name == "login_password",
            )
        ).first()
        assert secret is not None
        assert secret.last_used_at is not None

    deleted = client.delete(
        f"/projects/{project_id}/browser-secrets/login_password",
        headers=admin_headers,
    )
    assert deleted.status_code == 200, deleted.text

    listed_after_delete = client.get(f"/projects/{project_id}/browser-secrets", headers=admin_headers)
    assert listed_after_delete.status_code == 200, listed_after_delete.text
    assert listed_after_delete.json() == []


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
    assert payload["artifacts"][0]["view_url"].endswith(f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact_id}/view")

    download = client.get(
        f"/projects/{project_id}/incidents/{incident_id}/artifacts/{artifact_id}",
        headers={"X-API-KEY": api_key},
    )
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("image/png")
    assert download.content == b"png"


def test_incident_artifact_preview_parses_report_and_har(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_browser_preview.sqlite'}"
    os.environ["BROWSER_CHECK_ARTIFACT_DIR"] = str(tmp_path / "browser_preview_artifacts")

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import BrowserCheckArtifact, Check, CheckResult, CheckStatus, CheckType, Incident

    dbmod.create_db_and_tables()
    client = TestClient(app)

    create_project = client.post("/projects/", json={"name": "browser-preview"})
    assert create_project.status_code == 201, create_project.text
    project_payload = create_project.json()
    project_id = project_payload["project"]["id"]
    api_key = project_payload["api_key"]

    artifact_root = tmp_path / "browser_preview_artifacts" / f"project-{project_id}" / "check-1" / "run-1"
    artifact_root.mkdir(parents=True, exist_ok=True)
    report_file = artifact_root / "browser_report.json"
    report_file.write_text(
        json.dumps(
            {
                "attempt": 1,
                "failure_reason": "browser_step_failed:2:expect_visible:timeout",
                "start_url": "https://example.com/login",
                "final_url": "https://example.com/checkout",
                "page_title": "Checkout",
                "step_results": [
                    {"index": 0, "action": "goto", "status": "ok"},
                    {"index": 1, "action": "click", "status": "failed", "error": "timeout"},
                ],
                "console": [{"type": "error", "text": "boom"}],
                "page_errors": [{"message": "Unhandled promise rejection"}],
                "network_failures": [{"url": "https://api.example.com/pay", "method": "POST", "error_text": "net::ERR_CONNECTION_RESET"}],
                "http_errors": [{"url": "https://example.com/api/login", "status": 500, "status_text": "Internal Server Error"}],
            }
        ),
        encoding="utf-8",
    )
    har_file = artifact_root / "network.har"
    har_file.write_text(
        json.dumps(
            {
                "log": {
                    "pages": [{"id": "page_1"}],
                    "entries": [
                        {
                            "time": 48.3,
                            "request": {"method": "GET", "url": "https://example.com/api/login"},
                            "response": {"status": 500, "content": {"mimeType": "application/json"}},
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    with Session(dbmod.engine) as session:
        check = Check(
            project_id=project_id,
            name="browser-preview-check",
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

        report_artifact = BrowserCheckArtifact(
            project_id=project_id,
            check_id=check.id,
            check_result_id=result.id,
            incident_id=incident.id,
            artifact_type="report",
            file_path=str(report_file.resolve()),
            content_type="application/json",
            size_bytes=report_file.stat().st_size,
        )
        har_artifact = BrowserCheckArtifact(
            project_id=project_id,
            check_id=check.id,
            check_result_id=result.id,
            incident_id=incident.id,
            artifact_type="har",
            file_path=str(har_file.resolve()),
            content_type="application/json",
            size_bytes=har_file.stat().st_size,
        )
        session.add(report_artifact)
        session.add(har_artifact)
        session.commit()
        session.refresh(report_artifact)
        session.refresh(har_artifact)

        incident_id = incident.id
        report_artifact_id = report_artifact.id
        har_artifact_id = har_artifact.id

    report_preview = client.get(
        f"/projects/{project_id}/incidents/{incident_id}/artifacts/{report_artifact_id}/view",
        headers={"X-API-KEY": api_key},
    )
    assert report_preview.status_code == 200, report_preview.text
    report_payload = report_preview.json()
    assert report_payload["mode"] == "report"
    assert report_payload["summary"]["failure_reason"] == "browser_step_failed:2:expect_visible:timeout"
    assert len(report_payload["summary"]["step_results"]) == 2
    assert report_payload["summary"]["console"][0]["text"] == "boom"
    assert report_payload["raw_json"]["final_url"] == "https://example.com/checkout"

    har_preview = client.get(
        f"/projects/{project_id}/incidents/{incident_id}/artifacts/{har_artifact_id}/view",
        headers={"X-API-KEY": api_key},
    )
    assert har_preview.status_code == 200, har_preview.text
    har_payload = har_preview.json()
    assert har_payload["mode"] == "har"
    assert har_payload["summary"]["pages"] == 1
    assert har_payload["summary"]["entry_count"] == 1
    assert har_payload["summary"]["error_count"] == 1
    assert har_payload["summary"]["requests"][0]["status"] == 500


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

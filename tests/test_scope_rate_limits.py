from fastapi.testclient import TestClient


def _clear_scope_counters():
    from src import deps

    deps._public_counters.clear()
    deps._user_counters.clear()


def test_auth_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_auth_scope.sqlite'}")
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("AUTH_IP_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    first = client.post("/users/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert first.status_code == 401, first.text

    second = client.post("/users/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert second.status_code == 429, second.text


def test_public_status_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_public_status_scope.sqlite'}")
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("PUBLIC_STATUS_IP_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    created = client.post("/projects/", json={"name": "status-scope"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["id"]

    first = client.get(f"/ui/status/{project_id}/data")
    assert first.status_code == 200, first.text

    second = client.get(f"/ui/status/{project_id}/data")
    assert second.status_code == 429, second.text


def test_webhook_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_webhook_scope.sqlite'}")
    monkeypatch.setenv("WEBHOOK_IP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("WEBHOOK_API_KEY_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    created = client.post("/projects/", json={"name": "webhook-scope"})
    assert created.status_code == 201, created.text
    payload = created.json()
    project_id = payload["project"]["id"]
    api_key = payload["api_key"]

    first = client.post(
        f"/projects/{project_id}/webhook",
        json={"check_name": "scope-check", "event": "down"},
        headers={"X-API-KEY": api_key},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/projects/{project_id}/webhook",
        json={"check_name": "scope-check", "event": "down"},
        headers={"X-API-KEY": api_key},
    )
    assert second.status_code == 429, second.text


def test_browser_check_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_browser_scope.sqlite'}")
    monkeypatch.setenv("BROWSER_CHECK_API_KEY_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    created = client.post("/projects/", json={"name": "browser-scope"})
    assert created.status_code == 201, created.text
    payload = created.json()
    project_id = payload["project"]["id"]
    api_key = payload["api_key"]

    def browser_payload(name: str):
        return {
            "name": name,
            "type": "browser",
            "url": "https://example.com/login",
            "browser_steps": [
                {"action": "goto", "value": "https://example.com/login"},
            ],
            "browser_capture_screenshot": True,
        }

    first = client.post(
        f"/projects/{project_id}/checks/",
        json=browser_payload("browser-one"),
        headers={"X-API-KEY": api_key},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/projects/{project_id}/checks/",
        json=browser_payload("browser-two"),
        headers={"X-API-KEY": api_key},
    )
    assert second.status_code == 429, second.text


def test_admin_api_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_admin_scope.sqlite'}")
    monkeypatch.setenv("ADMIN_TOKEN", "scope-admin-token")
    monkeypatch.setenv("ADMIN_API_IP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("ADMIN_API_ADMIN_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    first = client.get("/admin/apikeys/", headers={"X-ADMIN-TOKEN": "scope-admin-token"})
    assert first.status_code == 200, first.text

    second = client.get("/admin/apikeys/", headers={"X-ADMIN-TOKEN": "scope-admin-token"})
    assert second.status_code == 429, second.text


def test_integration_action_scope_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db_integration_scope.sqlite'}")
    monkeypatch.setenv("ADMIN_TOKEN", "scope-admin-token")
    monkeypatch.setenv("INTEGRATION_ACTION_IP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("INTEGRATION_ACTION_ADMIN_RATE_LIMIT_PER_MINUTE", "1")

    from src import db as dbmod
    from src.main import app

    _clear_scope_counters()
    dbmod.create_db_and_tables()
    client = TestClient(app)

    created = client.post("/projects/", json={"name": "integration-scope"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["id"]

    first = client.post(f"/projects/{project_id}/pagerduty-test", headers={"X-ADMIN-TOKEN": "scope-admin-token"})
    assert first.status_code == 400, first.text

    second = client.post(f"/projects/{project_id}/pagerduty-test", headers={"X-ADMIN-TOKEN": "scope-admin-token"})
    assert second.status_code == 429, second.text

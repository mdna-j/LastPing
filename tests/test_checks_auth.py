import os

from fastapi.testclient import TestClient


def test_returned_project_api_key_can_create_check(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_project_bootstrap.sqlite'}"

    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    create_project = client.post("/projects/", json={"name": "ci-bootstrap"})
    assert create_project.status_code == 201, create_project.text
    payload = create_project.json()
    project_id = payload["project"]["id"]
    api_key = payload["api_key"]

    create_check = client.post(
        f"/projects/{project_id}/checks/",
        json={"name": "ci-us-east", "type": "heartbeat", "region": "us-east"},
        headers={"X-API-KEY": api_key},
    )
    assert create_check.status_code == 201, create_check.text
    assert create_check.json()["project_id"] == project_id


def test_check_write_endpoints_preserve_api_key_errors(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_checks_auth.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="checks-auth-project", api_key_hash=hash_api_key("valid-key"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    payload = {"name": "auth-check", "type": "heartbeat"}

    missing = client.post(f"/projects/{project_id}/checks/", json=payload)
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Missing API key"

    invalid_bearer = client.post(
        f"/projects/{project_id}/checks/",
        json=payload,
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert invalid_bearer.status_code == 403
    assert invalid_bearer.json()["detail"] == "Invalid API key"

    invalid_header = client.post(
        f"/projects/{project_id}/checks/",
        json=payload,
        headers={"X-API-KEY": "wrong-key"},
    )
    assert invalid_header.status_code == 403
    assert invalid_header.json()["detail"] == "Invalid API key"

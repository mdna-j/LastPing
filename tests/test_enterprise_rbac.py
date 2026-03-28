import os
from datetime import datetime, timedelta
from secrets import token_urlsafe

from fastapi.testclient import TestClient
from sqlmodel import Session


def _create_user_with_token(session, *, email, password_hash):
    from src.models import User, UserToken

    user = User(email=email, hashed_password=password_hash)
    session.add(user)
    session.commit()
    session.refresh(user)
    token = token_urlsafe(16)
    session.add(
        UserToken(
            user_id=user.id,
            token=token,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
    )
    session.commit()
    return user, token


def test_org_team_editor_access_can_manage_checks(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_enterprise_rbac_org.sqlite'}"

    from src import db as dbmod
    from src.main import app
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        owner, owner_token = _create_user_with_token(session, email="owner@example.com", password_hash=hash_password("pw"))
        editor, editor_token = _create_user_with_token(session, email="editor@example.com", password_hash=hash_password("pw"))

    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    editor_headers = {"Authorization": f"Bearer {editor_token}"}

    created_org = client.post("/orgs/", json={"name": "Acme Ops"}, headers=owner_headers)
    assert created_org.status_code == 201, created_org.text
    org_id = created_org.json()["id"]

    added_member = client.post(
        f"/orgs/{org_id}/members",
        json={"email": "editor@example.com", "role": "member"},
        headers=owner_headers,
    )
    assert added_member.status_code == 200, added_member.text

    created_team = client.post(
        f"/orgs/{org_id}/teams",
        json={"name": "SRE"},
        headers=owner_headers,
    )
    assert created_team.status_code == 201, created_team.text
    team_id = created_team.json()["id"]

    added_team_member = client.post(
        f"/orgs/{org_id}/teams/{team_id}/members",
        json={"email": "editor@example.com", "role": "member"},
        headers=owner_headers,
    )
    assert added_team_member.status_code == 200, added_team_member.text

    created_project = client.post(
        "/projects/",
        json={"name": "Acme API", "org_id": org_id},
        headers=owner_headers,
    )
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["project"]["id"]
    assert created_project.json()["project"]["org_id"] == org_id

    granted_access = client.post(
        f"/orgs/{org_id}/teams/{team_id}/projects/{project_id}",
        json={"role": "editor"},
        headers=owner_headers,
    )
    assert granted_access.status_code == 200, granted_access.text

    editor_role = client.get(f"/users/projects/{project_id}/role", headers=editor_headers)
    assert editor_role.status_code == 200
    assert editor_role.json()["role"] == "editor"

    created_check = client.post(
        f"/projects/{project_id}/checks/",
        json={"name": "team-editor-check", "type": "heartbeat"},
        headers=editor_headers,
    )
    assert created_check.status_code == 201, created_check.text


def test_scoped_project_tokens_and_audit_filters(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_enterprise_rbac_tokens.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "enterprise-admin"

    from src import db as dbmod
    from src.main import app
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        owner, owner_token = _create_user_with_token(session, email="owner@example.com", password_hash=hash_password("pw"))

    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    created_project = client.post("/projects/", json={"name": "Scoped Tokens"}, headers=owner_headers)
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["project"]["id"]

    viewer_token_resp = client.post(
        f"/projects/{project_id}/tokens",
        json={"name": "read-only", "role": "viewer"},
        headers=owner_headers,
    )
    assert viewer_token_resp.status_code == 201, viewer_token_resp.text
    viewer_key = viewer_token_resp.json()["api_key"]

    editor_token_resp = client.post(
        f"/projects/{project_id}/tokens",
        json={"name": "writer", "role": "editor"},
        headers=owner_headers,
    )
    assert editor_token_resp.status_code == 201, editor_token_resp.text
    editor_key = editor_token_resp.json()["api_key"]

    viewer_write = client.post(
        f"/projects/{project_id}/checks/",
        json={"name": "viewer-fail", "type": "heartbeat"},
        headers={"X-API-KEY": viewer_key},
    )
    assert viewer_write.status_code == 403
    assert "write access" in viewer_write.json()["detail"].lower()

    editor_write = client.post(
        f"/projects/{project_id}/checks/",
        json={"name": "editor-pass", "type": "heartbeat"},
        headers={"X-API-KEY": editor_key},
    )
    assert editor_write.status_code == 201, editor_write.text

    listed_tokens = client.get(f"/projects/{project_id}/tokens", headers=owner_headers)
    assert listed_tokens.status_code == 200, listed_tokens.text
    roles = {row["name"]: row["role"] for row in listed_tokens.json()}
    assert roles["read-only"] == "viewer"
    assert roles["writer"] == "editor"

    audit_rows = client.get(
        f"/admin/apikeys/audit/search?project_id={project_id}&action=create_scoped_project_token",
        headers={"X-ADMIN-TOKEN": "enterprise-admin"},
    )
    assert audit_rows.status_code == 200, audit_rows.text
    assert audit_rows.json()["total"] >= 2

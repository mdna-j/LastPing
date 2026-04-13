import os
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def test_admin_login_requires_mfa_enrollment_and_returns_org_roles(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_enterprise_auth_admin.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.enterprise_auth import generate_totp_code
    from src.main import app
    from src.models import OrgRole, Organization, OrganizationMembership, User, UserToken
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        user = User(email="admin@example.com", hashed_password=hash_password("StrongPassword1"), display_name="Admin User")
        session.add(user)
        session.commit()
        session.refresh(user)
        org = Organization(name="Acme Ops", slug="acme-ops")
        session.add(org)
        session.commit()
        session.refresh(org)
        session.add(
            OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=OrgRole.ADMIN.value,
            )
        )
        session.commit()

    login = client.post("/users/login", json={"email": "admin@example.com", "password": "StrongPassword1"})
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["mfa_setup_required"] is True
    assert body["mfa_enforced"] is True
    assert body["access_token"] is None
    assert body["mfa_enrollment_secret"]

    enable = client.post(
        "/users/mfa/enable",
        json={
            "challenge_token": body["mfa_challenge_token"],
            "code": generate_totp_code(body["mfa_enrollment_secret"]),
        },
    )
    assert enable.status_code == 200, enable.text
    token = enable.json()["access_token"]
    assert token

    me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["mfa_enabled"] is True
    assert me_body["organizations"][0]["organization_name"] == "Acme Ops"
    assert me_body["organizations"][0]["role"] == "admin"

    with Session(dbmod.engine) as session:
        user = session.exec(select(User).where(User.email == "admin@example.com")).first()
        assert user is not None
        assert user.mfa_enabled_at is not None
        session_row = session.exec(select(UserToken).where(UserToken.user_id == user.id)).first()
        assert session_row is not None
        assert session_row.mfa_verified_at is not None


def test_mfa_login_verification_and_session_revocation(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_enterprise_auth_mfa.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.enterprise_auth import generate_totp_code, generate_totp_secret
    from src.main import app
    from src.models import User
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    secret = generate_totp_secret()
    with Session(dbmod.engine) as session:
        session.add(
            User(
                email="mfa-user@example.com",
                hashed_password=hash_password("StrongPassword1"),
                mfa_secret=secret,
                mfa_enabled_at=datetime.utcnow() - timedelta(minutes=2),
            )
        )
        session.commit()

    login = client.post("/users/login", json={"email": "mfa-user@example.com", "password": "StrongPassword1"})
    assert login.status_code == 200, login.text
    assert login.json()["mfa_required"] is True

    verify = client.post(
        "/users/mfa/login/verify",
        json={
            "challenge_token": login.json()["mfa_challenge_token"],
            "code": generate_totp_code(secret),
        },
    )
    assert verify.status_code == 200, verify.text
    token = verify.json()["access_token"]
    assert token

    sessions = client.get("/users/sessions", headers={"Authorization": f"Bearer {token}"})
    assert sessions.status_code == 200, sessions.text
    session_rows = sessions.json()["sessions"]
    assert len(session_rows) == 1
    assert session_rows[0]["mfa_verified_at"] is not None

    revoke = client.delete(f"/users/sessions/{session_rows[0]['id']}", headers={"Authorization": f"Bearer {token}"})
    assert revoke.status_code == 200, revoke.text

    me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401
    assert me.json()["detail"] == "Token revoked"


def test_sso_callback_creates_linked_identity_and_browser_session(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_enterprise_auth_sso.sqlite'}"
    os.environ["SSO_GOOGLE_CLIENT_ID"] = "client-id"
    os.environ["SSO_GOOGLE_CLIENT_SECRET"] = "client-secret"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import OrgRole, Organization, OrganizationMembership, User
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        user = User(email="sso-user@example.com", hashed_password=hash_password("StrongPassword1"), display_name="SSO User")
        session.add(user)
        session.commit()
        session.refresh(user)
        org = Organization(name="SSO Org", slug="sso-org")
        session.add(org)
        session.commit()
        session.refresh(org)
        session.add(
            OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=OrgRole.OWNER.value,
            )
        )
        session.commit()

    monkeypatch.setattr(
        "src.routers.users.exchange_sso_code",
        lambda provider, code, redirect_uri: {"access_token": "google-access-token"},
    )
    monkeypatch.setattr(
        "src.routers.users.fetch_sso_profile",
        lambda provider, token_payload: {
            "subject": "google-subject-123",
            "email": "sso-user@example.com",
            "display_name": "SSO User",
        },
    )

    start = client.get("/users/sso/google/start?redirect_to=/ui/account", follow_redirects=False)
    assert start.status_code == 302
    redirect = urlparse(start.headers["location"])
    state = parse_qs(redirect.query)["state"][0]

    callback = client.get(f"/users/sso/google/callback?code=test-code&state={state}&return_json=true")
    assert callback.status_code == 200, callback.text
    body = callback.json()
    assert body["access_token"]
    assert body["auth_provider"] == "google"

    me = client.get("/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["organizations"][0]["role"] == "owner"
    assert me_body["linked_identities"][0]["provider"] == "google"


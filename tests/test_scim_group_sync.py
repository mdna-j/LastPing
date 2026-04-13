import json
import os
from datetime import datetime, timedelta
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session, select


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


def test_sso_group_mapping_syncs_org_and_team_roles(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_scim_group_sync_sso.sqlite'}"
    os.environ["SSO_GOOGLE_CLIENT_ID"] = "client-id"
    os.environ["SSO_GOOGLE_CLIENT_SECRET"] = "client-secret"

    from src import db as dbmod
    from src.main import app
    from src.models import OrgRole, Organization, OrganizationGroupMapping, OrganizationMembership, Team, TeamGroupMapping, TeamMembership, TeamRole, User, UserIdentity

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        org = Organization(name="Group Sync Org", slug="group-sync-org")
        session.add(org)
        session.commit()
        session.refresh(org)
        team = Team(organization_id=org.id, name="Platform", slug="platform")
        session.add(team)
        session.commit()
        session.refresh(team)
        session.add(
            OrganizationGroupMapping(
                organization_id=org.id,
                provider="google",
                external_group="Acme-Admins",
                role=OrgRole.ADMIN.value,
            )
        )
        session.add(
            TeamGroupMapping(
                organization_id=org.id,
                team_id=team.id,
                provider="google",
                external_group="Platform-Oncall",
                role=TeamRole.LEAD.value,
            )
        )
        session.commit()
        org_id = org.id
        team_id = team.id

    monkeypatch.setattr(
        "src.routers.users.exchange_sso_code",
        lambda provider, code, redirect_uri: {"access_token": "google-access-token"},
    )
    monkeypatch.setattr(
        "src.routers.users.fetch_sso_profile",
        lambda provider, token_payload: {
            "subject": "google-subject-123",
            "email": "group-sync@example.com",
            "display_name": "Group Sync User",
            "groups": ["Platform-Oncall", "Acme-Admins"],
        },
    )

    start = client.get("/users/sso/google/start?redirect_to=/ui/account", follow_redirects=False)
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    callback = client.get(f"/users/sso/google/callback?code=test-code&state={state}&return_json=true")
    assert callback.status_code == 200, callback.text

    with Session(dbmod.engine) as session:
        user = session.exec(select(User).where(User.email == "group-sync@example.com")).first()
        assert user is not None
        org_membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        assert org_membership is not None
        assert org_membership.role == OrgRole.ADMIN.value

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        assert team_membership is not None
        assert team_membership.role == TeamRole.LEAD.value

        identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == "google",
            )
        ).first()
        assert identity is not None
        assert json.loads(identity.last_groups_json) == ["Acme-Admins", "Platform-Oncall"]


def test_scim_token_rotation_and_org_scoped_provisioning(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_scim_group_sync_provisioning.sqlite'}"

    from src import db as dbmod
    from src.main import app
    from src.models import OrgRole, OrganizationMembership, TeamMembership
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        owner, owner_token = _create_user_with_token(
            session,
            email="owner@example.com",
            password_hash=hash_password("StrongPassword1"),
        )
        owner_id = owner.id

    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    created_org = client.post("/orgs/", json={"name": "SCIM Org"}, headers=owner_headers)
    assert created_org.status_code == 201, created_org.text
    org_id = created_org.json()["id"]

    created_team = client.post(
        f"/orgs/{org_id}/teams",
        json={"name": "Platform"},
        headers=owner_headers,
    )
    assert created_team.status_code == 201, created_team.text
    team_id = created_team.json()["id"]

    created_org_mapping = client.post(
        f"/orgs/{org_id}/group-mappings/org",
        json={"provider": "scim", "external_group": "admins", "role": "admin"},
        headers=owner_headers,
    )
    assert created_org_mapping.status_code == 200, created_org_mapping.text

    created_team_mapping = client.post(
        f"/orgs/{org_id}/group-mappings/team/{team_id}",
        json={"provider": "scim", "external_group": "platform", "role": "lead"},
        headers=owner_headers,
    )
    assert created_team_mapping.status_code == 200, created_team_mapping.text

    rotated = client.post(f"/orgs/{org_id}/scim-settings/rotate", headers=owner_headers)
    assert rotated.status_code == 200, rotated.text
    scim_token = rotated.json()["bearer_token"]
    scim_headers = {"Authorization": f"Bearer {scim_token}"}

    created_user = client.post(
        "/scim/v2/Users",
        headers=scim_headers,
        json={
            "userName": "scim-user@example.com",
            "displayName": "SCIM User",
            "externalId": "ext-123",
            "active": True,
            "groups": [{"display": "admins"}, {"display": "platform"}],
        },
    )
    assert created_user.status_code == 201, created_user.text
    assert created_user.json()["active"] is True

    listed_users = client.get("/scim/v2/Users", headers=scim_headers)
    assert listed_users.status_code == 200, listed_users.text
    assert listed_users.json()["totalResults"] == 2
    user_id = int(created_user.json()["id"])

    with Session(dbmod.engine) as session:
        owner_membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == owner_id,
            )
        ).first()
        assert owner_membership is not None

        membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user_id,
            )
        ).first()
        assert membership is not None
        assert membership.role == OrgRole.ADMIN.value

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        ).first()
        assert team_membership is not None
        assert team_membership.role == "lead"

    deactivated = client.patch(
        f"/scim/v2/Users/{user_id}",
        headers=scim_headers,
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False

    fetched = client.get(f"/scim/v2/Users/{user_id}", headers=scim_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["active"] is False

    listed_after = client.get("/scim/v2/Users", headers=scim_headers)
    assert listed_after.status_code == 200, listed_after.text
    assert listed_after.json()["totalResults"] == 1

    with Session(dbmod.engine) as session:
        membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user_id,
            )
        ).first()
        assert membership is None

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        ).first()
        assert team_membership is None

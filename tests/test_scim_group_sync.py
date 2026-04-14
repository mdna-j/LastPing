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

    monkeypatch.setattr(
        "src.routers.users.fetch_sso_profile",
        lambda provider, token_payload: {
            "subject": "google-subject-123",
            "email": "group-sync@example.com",
            "display_name": "Group Sync User",
            "groups": [],
        },
    )

    start = client.get("/users/sso/google/start?redirect_to=/ui/account", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/users/sso/google/callback?code=test-code-2&state={state}&return_json=true")
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
        assert org_membership is None

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        assert team_membership is None

        identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == "google",
            )
        ).first()
        assert identity is not None
        assert json.loads(identity.last_groups_json) == []


def test_sso_group_sync_reverts_to_manual_roles(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_scim_group_sync_revert.sqlite'}"
    os.environ["SSO_GOOGLE_CLIENT_ID"] = "client-id"
    os.environ["SSO_GOOGLE_CLIENT_SECRET"] = "client-secret"

    from src import db as dbmod
    from src.main import app
    from src.models import (
        OrgRole,
        Organization,
        OrganizationGroupMapping,
        OrganizationMembership,
        Team,
        TeamGroupMapping,
        TeamMembership,
        TeamRole,
        User,
        UserIdentity,
    )
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        org = Organization(name="Manual Fallback Org", slug="manual-fallback-org")
        session.add(org)
        session.commit()
        session.refresh(org)
        team = Team(organization_id=org.id, name="Ops", slug="ops")
        session.add(team)
        session.commit()
        session.refresh(team)
        user = User(email="fallback@example.com", hashed_password=hash_password("StrongPassword1"))
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(
            OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=OrgRole.MEMBER.value,
            )
        )
        session.add(
            TeamMembership(
                team_id=team.id,
                user_id=user.id,
                role=TeamRole.MEMBER.value,
            )
        )
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
                external_group="Ops-Leads",
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
            "subject": "google-subject-manual",
            "email": "fallback@example.com",
            "display_name": "Fallback User",
            "groups": ["Acme-Admins", "Ops-Leads"],
        },
    )

    start = client.get("/users/sso/google/start?redirect_to=/ui/account", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/users/sso/google/callback?code=manual-1&state={state}&return_json=true")
    assert callback.status_code == 200, callback.text

    with Session(dbmod.engine) as session:
        user = session.exec(select(User).where(User.email == "fallback@example.com")).first()
        assert user is not None
        org_membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        assert org_membership is not None
        assert org_membership.role == OrgRole.ADMIN.value
        assert org_membership.managed_fallback_role == OrgRole.MEMBER.value

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        assert team_membership is not None
        assert team_membership.role == TeamRole.LEAD.value
        assert team_membership.managed_fallback_role == TeamRole.MEMBER.value

    monkeypatch.setattr(
        "src.routers.users.fetch_sso_profile",
        lambda provider, token_payload: {
            "subject": "google-subject-manual",
            "email": "fallback@example.com",
            "display_name": "Fallback User",
            "groups": [],
        },
    )
    start = client.get("/users/sso/google/start?redirect_to=/ui/account", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/users/sso/google/callback?code=manual-2&state={state}&return_json=true")
    assert callback.status_code == 200, callback.text

    with Session(dbmod.engine) as session:
        user = session.exec(select(User).where(User.email == "fallback@example.com")).first()
        assert user is not None
        org_membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        assert org_membership is not None
        assert org_membership.role == OrgRole.MEMBER.value
        assert org_membership.managed_provider is None

        team_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        assert team_membership is not None
        assert team_membership.role == TeamRole.MEMBER.value
        assert team_membership.managed_provider is None

        identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == "google",
            )
        ).first()
        assert identity is not None
        assert json.loads(identity.last_groups_json) == []


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

    reprovisioned = client.post(
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
    assert reprovisioned.status_code == 201, reprovisioned.text

    deleted = client.delete(f"/scim/v2/Users/{user_id}", headers=scim_headers)
    assert deleted.status_code == 204, deleted.text

    missing = client.get(f"/scim/v2/Users/{user_id}", headers=scim_headers)
    assert missing.status_code == 404


def test_scim_groups_resource_syncs_team_mapping_and_members(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_scim_groups_resource.sqlite'}"

    from src import db as dbmod
    from src.main import app
    from src.models import TeamGroupMapping, TeamMembership, UserIdentity
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
    created_org = client.post("/orgs/", json={"name": "SCIM Groups Org"}, headers=owner_headers)
    assert created_org.status_code == 201, created_org.text
    org_id = created_org.json()["id"]

    rotated = client.post(f"/orgs/{org_id}/scim-settings/rotate", headers=owner_headers)
    assert rotated.status_code == 200, rotated.text
    scim_headers = {"Authorization": f"Bearer {rotated.json()['bearer_token']}"}

    alice = client.post(
        "/scim/v2/Users",
        headers=scim_headers,
        json={"userName": "alice@example.com", "displayName": "Alice", "externalId": "alice-ext", "active": True},
    )
    assert alice.status_code == 201, alice.text
    alice_id = int(alice.json()["id"])

    bob = client.post(
        "/scim/v2/Users",
        headers=scim_headers,
        json={"userName": "bob@example.com", "displayName": "Bob", "externalId": "bob-ext", "active": True},
    )
    assert bob.status_code == 201, bob.text
    bob_id = int(bob.json()["id"])

    created_group = client.post(
        "/scim/v2/Groups",
        headers=scim_headers,
        json={
            "displayName": "Platform Primary",
            "members": [{"value": str(alice_id)}],
            "urn:lastping:schemas:scim:group:1.0": {"role": "lead"},
        },
    )
    assert created_group.status_code == 201, created_group.text
    group_body = created_group.json()
    group_id = int(group_body["id"])
    assert group_body["displayName"] == "Platform Primary"
    assert group_body["urn:lastping:schemas:scim:group:1.0"]["role"] == "lead"
    assert len(group_body["members"]) == 1

    listed_groups = client.get("/scim/v2/Groups", headers=scim_headers)
    assert listed_groups.status_code == 200, listed_groups.text
    assert listed_groups.json()["totalResults"] == 1

    fetched_group = client.get(f"/scim/v2/Groups/{group_id}", headers=scim_headers)
    assert fetched_group.status_code == 200, fetched_group.text
    assert fetched_group.json()["displayName"] == "Platform Primary"

    with Session(dbmod.engine) as session:
        mapping = session.get(TeamGroupMapping, group_id)
        assert mapping is not None
        assert mapping.provider == "scim"
        assert mapping.external_group == "Platform Primary"
        assert mapping.role == "lead"

        alice_identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == alice_id,
                UserIdentity.provider == "scim",
                UserIdentity.provider_subject.like(f"{org_id}:%"),
            )
        ).first()
        assert alice_identity is not None
        assert json.loads(alice_identity.last_groups_json) == ["Platform Primary"]

        alice_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == mapping.team_id,
                TeamMembership.user_id == alice_id,
            )
        ).first()
        assert alice_membership is not None
        assert alice_membership.role == "lead"

    patched_group = client.patch(
        f"/scim/v2/Groups/{group_id}",
        headers=scim_headers,
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Platform Backup"},
                {"op": "replace", "path": "members", "value": [{"value": str(bob_id)}]},
            ],
        },
    )
    assert patched_group.status_code == 200, patched_group.text
    assert patched_group.json()["displayName"] == "Platform Backup"
    assert len(patched_group.json()["members"]) == 1
    assert patched_group.json()["members"][0]["value"] == str(bob_id)

    with Session(dbmod.engine) as session:
        mapping = session.get(TeamGroupMapping, group_id)
        assert mapping is not None
        assert mapping.external_group == "Platform Backup"

        alice_identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == alice_id,
                UserIdentity.provider == "scim",
                UserIdentity.provider_subject.like(f"{org_id}:%"),
            )
        ).first()
        assert alice_identity is not None
        assert json.loads(alice_identity.last_groups_json) == []

        bob_identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == bob_id,
                UserIdentity.provider == "scim",
                UserIdentity.provider_subject.like(f"{org_id}:%"),
            )
        ).first()
        assert bob_identity is not None
        assert json.loads(bob_identity.last_groups_json) == ["Platform Backup"]

        alice_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == mapping.team_id,
                TeamMembership.user_id == alice_id,
            )
        ).first()
        assert alice_membership is None

        bob_membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == mapping.team_id,
                TeamMembership.user_id == bob_id,
            )
        ).first()
        assert bob_membership is not None
        assert bob_membership.role == "lead"

    deleted = client.delete(f"/scim/v2/Groups/{group_id}", headers=scim_headers)
    assert deleted.status_code == 204, deleted.text

    listed_groups = client.get("/scim/v2/Groups", headers=scim_headers)
    assert listed_groups.status_code == 200, listed_groups.text
    assert listed_groups.json()["totalResults"] == 0

    with Session(dbmod.engine) as session:
        mapping = session.get(TeamGroupMapping, group_id)
        assert mapping is None

        bob_identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == bob_id,
                UserIdentity.provider == "scim",
                UserIdentity.provider_subject.like(f"{org_id}:%"),
            )
        ).first()
        assert bob_identity is not None
        assert json.loads(bob_identity.last_groups_json) == []

        owner_membership = session.exec(
            select(TeamMembership).where(TeamMembership.user_id == owner_id)
        ).all()
        assert owner_membership == []

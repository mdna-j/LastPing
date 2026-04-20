import os

from fastapi.testclient import TestClient


def test_tenant_console_org_overview_service_accounts_and_audit(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_tenant_management.sqlite'}"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import Organization, OrganizationMembership, OrgRole, Project, Team, User, UserToken
    from src.security import hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    bearer = "tenant-owner-session"

    with Session(dbmod.engine) as session:
        user = User(email="owner@example.com", hashed_password=hash_password("StrongPassword1"), display_name="Owner")
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
        session.add(UserToken(user_id=user.id, token=bearer))
        session.commit()

        team = Team(organization_id=org.id, name="Platform", slug="platform")
        session.add(team)
        session.commit()
        session.refresh(team)

        project = Project(name="Checkout", org_id=org.id)
        session.add(project)
        session.commit()
        session.refresh(project)

        org_id = org.id
        team_id = team.id
        project_id = project.id

    headers = {"Authorization": f"Bearer {bearer}"}

    tenant_ui = client.get("/ui/tenant")
    assert tenant_ui.status_code == 200
    assert "Tenant Console" in tenant_ui.text
    assert "Organization Members" in tenant_ui.text
    assert "Team Memberships" in tenant_ui.text

    mine = client.get("/orgs/mine/overview", headers=headers)
    assert mine.status_code == 200, mine.text
    mine_body = mine.json()
    assert len(mine_body) == 1
    assert mine_body[0]["organization_name"] == "Acme Ops"
    assert mine_body[0]["role"] == "admin"
    assert mine_body[0]["project_count"] == 1

    owner_team = client.put(
        f"/orgs/{org_id}/projects/{project_id}/owner-team",
        headers=headers,
        json={"team_id": team_id},
    )
    assert owner_team.status_code == 200, owner_team.text
    assert owner_team.json()["team_name"] == "Platform"

    invite_org_member = client.post(
        f"/orgs/{org_id}/members",
        headers=headers,
        json={"email": "analyst@example.com", "role": "member"},
    )
    assert invite_org_member.status_code == 200, invite_org_member.text

    promote_org_member = client.post(
        f"/orgs/{org_id}/members",
        headers=headers,
        json={"email": "analyst@example.com", "role": "admin"},
    )
    assert promote_org_member.status_code == 200, promote_org_member.text
    analyst_user_id = promote_org_member.json()["user_id"]

    org_members = client.get(f"/orgs/{org_id}/members", headers=headers)
    assert org_members.status_code == 200, org_members.text
    org_members_body = org_members.json()
    assert any(row["email"] == "analyst@example.com" and row["role"] == "admin" for row in org_members_body)

    add_team_member = client.post(
        f"/orgs/{org_id}/teams/{team_id}/members",
        headers=headers,
        json={"email": "teammate@example.com", "role": "member"},
    )
    assert add_team_member.status_code == 200, add_team_member.text
    teammate_user_id = add_team_member.json()["user_id"]

    team_members = client.get(f"/orgs/{org_id}/teams/{team_id}/members", headers=headers)
    assert team_members.status_code == 200, team_members.text
    team_members_body = team_members.json()
    assert any(row["email"] == "teammate@example.com" and row["role"] == "member" for row in team_members_body)

    org_members_after_team_add = client.get(f"/orgs/{org_id}/members", headers=headers)
    assert org_members_after_team_add.status_code == 200, org_members_after_team_add.text
    assert any(
        row["email"] == "teammate@example.com" and row["role"] == "member"
        for row in org_members_after_team_add.json()
    )

    create_sa = client.post(
        f"/orgs/{org_id}/projects/{project_id}/service-accounts",
        headers=headers,
        json={
            "name": "deploy-bot",
            "description": "deploy pipeline credential",
            "role": "editor",
            "team_id": team_id,
            "rotation_interval_days": 30,
        },
    )
    assert create_sa.status_code == 201, create_sa.text
    sa_body = create_sa.json()
    assert sa_body["api_key"]
    assert sa_body["token"]["token_type"] == "service_account"
    assert sa_body["token"]["managed_by_team_name"] == "Platform"
    token_id = sa_body["token"]["id"]

    overview = client.get(f"/orgs/{org_id}/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    overview_body = overview.json()
    assert overview_body["current_role"] == "admin"
    assert overview_body["summary"]["service_account_count"] == 1
    assert overview_body["projects"][0]["owner_teams"][0]["id"] == team_id
    assert overview_body["projects"][0]["service_account_count"] == 1

    inventory = client.get(f"/orgs/{org_id}/token-inventory", headers=headers)
    assert inventory.status_code == 200, inventory.text
    inventory_body = inventory.json()
    assert inventory_body["summary"]["token_count"] == 1
    assert inventory_body["summary"]["service_account_count"] == 1
    assert inventory_body["tokens"][0]["project_name"] == "Checkout"
    assert inventory_body["tokens"][0]["managed_by_team_name"] == "Platform"
    assert inventory_body["tokens"][0]["rotation_interval_days"] == 30

    revoke = client.post(f"/orgs/{org_id}/tokens/{token_id}/revoke", headers=headers)
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["revoked"] is True

    remove_org_member = client.delete(f"/orgs/{org_id}/members/{teammate_user_id}", headers=headers)
    assert remove_org_member.status_code == 200, remove_org_member.text
    assert remove_org_member.json()["removed"] is True
    assert remove_org_member.json()["removed_team_memberships"] == 1

    team_members_after_remove = client.get(f"/orgs/{org_id}/teams/{team_id}/members", headers=headers)
    assert team_members_after_remove.status_code == 200, team_members_after_remove.text
    assert all(row["user_id"] != teammate_user_id for row in team_members_after_remove.json())

    org_members_after_remove = client.get(f"/orgs/{org_id}/members", headers=headers)
    assert org_members_after_remove.status_code == 200, org_members_after_remove.text
    assert all(row["user_id"] != teammate_user_id for row in org_members_after_remove.json())

    audit = client.get(f"/orgs/{org_id}/membership-audit", headers=headers)
    assert audit.status_code == 200, audit.text
    actions = [row["action"] for row in audit.json()["items"]]
    assert analyst_user_id > 0
    assert "set_project_owner_team" in actions
    assert "upsert_org_member" in actions
    assert "upsert_team_member" in actions
    assert "create_service_account_token" in actions
    assert "revoke_org_token" in actions
    assert "remove_org_member" in actions

import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_incident_endpoints_require_valid_project_api_key_and_support_share_flow(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incidents_auth.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Incident, Event, EventType
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        api_key = "incidentkey"
        project = Project(name="incident-auth-project", api_key_hash=hash_api_key(api_key))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="incident-check",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.DOWN,
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(minutes=10),
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            started_at=datetime.utcnow() - timedelta(minutes=5),
            status="open",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)

        session.add(
            Event(
                check_id=check.id,
                project_id=project.id,
                incident_id=incident.id,
                event_type=EventType.DOWN,
                message="heartbeat overdue",
                created_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        session.commit()

        project_id = project.id
        incident_id = incident.id

    missing = client.get(f"/projects/{project_id}/incidents")
    assert missing.status_code == 401

    invalid = client.get(
        f"/projects/{project_id}/incidents",
        headers={"X-API-KEY": "wrong-key"},
    )
    assert invalid.status_code == 403

    listed = client.get(
        f"/projects/{project_id}/incidents",
        headers={"X-API-KEY": api_key},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == incident_id
    assert rows[0]["status"] == "open"
    assert rows[0]["share_token"] is None

    detail = client.get(
        f"/projects/{project_id}/incidents/{incident_id}",
        headers={"X-API-KEY": api_key},
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["incident"]["id"] == incident_id
    assert payload["incident"]["check_id"] is not None
    assert len(payload["events"]) == 1
    assert payload["events"][0]["type"] == "down"

    share = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/share",
        headers={"X-API-KEY": api_key},
    )
    assert share.status_code == 200
    share_token = share.json()["share_token"]
    assert isinstance(share_token, str) and len(share_token) >= 10

    public = client.get(f"/incidents/public/{share_token}")
    assert public.status_code == 200
    public_payload = public.json()
    assert public_payload["project"]["status_page_url"] == f"/ui/status/{project_id}"
    assert public_payload["incident"]["id"] == incident_id
    assert public_payload["incident"]["share_url"] == f"/ui/incidents/public/{share_token}"
    assert len(public_payload["events"]) == 1
    assert len(public_payload["timeline"]) >= 2

    public_page = client.get(f"/ui/incidents/public/{share_token}")
    assert public_page.status_code == 200
    assert "Shared incident" in public_page.text


def test_incident_lifecycle_management_with_owner_token_and_viewer_read_access(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incident_lifecycle.sqlite'}"

    from secrets import token_urlsafe

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import (
        Check,
        CheckStatus,
        CheckType,
        Event,
        EventType,
        Incident,
        Project,
        ProjectMembership,
        User,
        UserToken,
    )
    from src.security import hash_api_key, hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="incident-lifecycle-project", api_key_hash=hash_api_key("project-key"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="incident-owner-check",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.DOWN,
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(minutes=12),
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            started_at=datetime.utcnow() - timedelta(minutes=8),
            status="open",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)

        session.add(
            Event(
                check_id=check.id,
                project_id=project.id,
                incident_id=incident.id,
                event_type=EventType.DOWN,
                message="heartbeat overdue",
                created_at=datetime.utcnow() - timedelta(minutes=8),
            )
        )

        owner = User(email="owner@example.com", hashed_password=hash_password("pw"))
        viewer = User(email="viewer@example.com", hashed_password=hash_password("pw"))
        session.add(owner)
        session.add(viewer)
        session.commit()
        session.refresh(owner)
        session.refresh(viewer)

        session.add(ProjectMembership(user_id=owner.id, project_id=project.id, role="owner"))
        session.add(ProjectMembership(user_id=viewer.id, project_id=project.id, role="viewer"))

        owner_token = token_urlsafe(16)
        viewer_token = token_urlsafe(16)
        session.add(UserToken(user_id=owner.id, token=owner_token, expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.add(UserToken(user_id=viewer.id, token=viewer_token, expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.commit()

        project_id = project.id
        incident_id = incident.id

    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    viewer_list = client.get(f"/projects/{project_id}/incidents", headers=viewer_headers)
    assert viewer_list.status_code == 200
    assert viewer_list.json()[0]["note_count"] == 0

    assign = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/assign",
        json={"owner": "alice"},
        headers=owner_headers,
    )
    assert assign.status_code == 200
    assert assign.json()["incident"]["owner"] == "alice"

    viewer_assign = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/assign",
        json={"owner": "bob"},
        headers=viewer_headers,
    )
    assert viewer_assign.status_code == 403

    ack = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/ack",
        json={"acknowledged": True},
        headers=owner_headers,
    )
    assert ack.status_code == 200
    assert ack.json()["incident"]["acknowledged_at"] is not None
    assert ack.json()["incident"]["acknowledged_by"].startswith("user:")

    silence_until = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
    silence = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/silence",
        json={"until": silence_until},
        headers=owner_headers,
    )
    assert silence.status_code == 200
    assert silence.json()["incident"]["silenced_until"] is not None

    note = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/notes",
        json={"body": "Investigating heartbeat backlog."},
        headers=owner_headers,
    )
    assert note.status_code == 200
    assert note.json()["note"]["body"] == "Investigating heartbeat backlog."
    assert note.json()["note"]["author"].startswith("user:")

    resolve = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/resolve",
        json={"summary": "Drained the backlog and verified fresh heartbeats across the region."},
        headers=owner_headers,
    )
    assert resolve.status_code == 200
    assert resolve.json()["incident"]["status"] == "resolved"
    assert resolve.json()["incident"]["resolved_at"] is not None
    assert resolve.json()["incident"]["resolved_by"].startswith("user:")
    assert "Drained the backlog" in resolve.json()["incident"]["resolution_summary"]

    reopen = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/reopen",
        json={"reason": "A fresh timeout showed up after the first fix."},
        headers=owner_headers,
    )
    assert reopen.status_code == 200
    assert reopen.json()["incident"]["status"] == "open"
    assert reopen.json()["incident"]["resolved_at"] is None
    assert reopen.json()["incident"]["resolution_summary"] is None

    detail = client.get(
        f"/projects/{project_id}/incidents/{incident_id}",
        headers=owner_headers,
    )
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["incident"]["owner"] == "alice"
    assert detail_payload["incident"]["note_count"] == 1
    assert len(detail_payload["notes"]) == 1
    assert detail_payload["notes"][0]["body"] == "Investigating heartbeat backlog."
    assert detail_payload["incident"]["status"] == "open"


def test_incident_workflow_actions_emit_slack_thread_updates(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incident_slack_updates.sqlite'}"

    from secrets import token_urlsafe

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, CheckType, Incident, Project, ProjectMembership, User, UserToken
    from src.security import hash_password

    calls = []

    def fake_notify(project, incident, *, action, body, check=None, session=None, share_url=None):
        calls.append(
            {
                "project_id": project.id,
                "incident_id": incident.id,
                "action": action,
                "body": body,
                "share_url": share_url,
            }
        )
        return True

    monkeypatch.setattr("src.routers.incidents.notify_incident_slack_update", fake_notify)

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="incident-slack-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="incident-check", type=CheckType.HEARTBEAT)
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            status="open",
            slack_thread_ts="1740000000.000123",
            slack_channel_id="COPS",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)

        owner = User(email="owner2@example.com", hashed_password=hash_password("pw"))
        session.add(owner)
        session.commit()
        session.refresh(owner)
        session.add(ProjectMembership(user_id=owner.id, project_id=project.id, role="owner"))

        owner_token = token_urlsafe(16)
        session.add(UserToken(user_id=owner.id, token=owner_token, expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.commit()

        project_id = project.id
        incident_id = incident.id

    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    share = client.post(f"/projects/{project_id}/incidents/{incident_id}/share", headers=owner_headers)
    assert share.status_code == 200

    assign = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/assign",
        json={"owner": "alice"},
        headers=owner_headers,
    )
    assert assign.status_code == 200

    ack = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/ack",
        json={"acknowledged": True},
        headers=owner_headers,
    )
    assert ack.status_code == 200

    note = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/notes",
        json={"body": "Watching Slack thread."},
        headers=owner_headers,
    )
    assert note.status_code == 200

    resolve = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/resolve",
        json={"summary": "Applied the queue patch and verified the incident stopped firing."},
        headers=owner_headers,
    )
    assert resolve.status_code == 200

    reopen = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/reopen",
        json={"reason": "A retry storm reopened the incident."},
        headers=owner_headers,
    )
    assert reopen.status_code == 200

    actions = [call["action"] for call in calls]
    assert actions == ["share", "assign", "ack", "note", "resolve", "reopen"]
    assert any(call["share_url"] for call in calls if call["action"] == "share")


def test_incident_acknowledge_emits_pagerduty_ack(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incident_pagerduty_ack.sqlite'}"

    from secrets import token_urlsafe

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Check, CheckType, Incident, Project, ProjectMembership, User, UserToken
    from src.security import hash_password

    calls = []

    def fake_pd_notify(project, incident, *, event_action, summary, check=None, session=None, severity="critical", custom_details=None):
        calls.append(
            {
                "project_id": project.id,
                "incident_id": incident.id,
                "event_action": event_action,
                "summary": summary,
                "check_id": getattr(check, "id", None) if check is not None else None,
                "custom_details": custom_details or {},
            }
        )
        return True

    monkeypatch.setattr("src.routers.incidents.notify_incident_pagerduty_update", fake_pd_notify)

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="incident-pd-project", pagerduty_integration_key="pd-key")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="incident-check", type=CheckType.HEARTBEAT, alert_pagerduty_enabled=True)
        session.add(check)
        session.commit()
        session.refresh(check)

        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            status="open",
            pagerduty_dedup_key=f"lastping:incident:{project.id}:1",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)

        owner = User(email="owner-pd@example.com", hashed_password=hash_password("pw"))
        session.add(owner)
        session.commit()
        session.refresh(owner)
        session.add(ProjectMembership(user_id=owner.id, project_id=project.id, role="owner"))

        owner_token = token_urlsafe(16)
        session.add(UserToken(user_id=owner.id, token=owner_token, expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.commit()

        project_id = project.id
        incident_id = incident.id

    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    ack = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/ack",
        json={"acknowledged": True},
        headers=owner_headers,
    )
    assert ack.status_code == 200
    assert len(calls) == 1
    assert calls[0]["event_action"] == "acknowledge"
    assert calls[0]["incident_id"] == incident_id

    resolve = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/resolve",
        json={"summary": "Service stabilized after reducing the load on the failing dependency."},
        headers=owner_headers,
    )
    assert resolve.status_code == 200
    assert len(calls) == 2
    assert calls[1]["event_action"] == "resolve"
    assert calls[1]["custom_details"]["resolution_summary"].startswith("Service stabilized")

    clear_ack = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/ack",
        json={"acknowledged": False},
        headers=owner_headers,
    )
    assert clear_ack.status_code == 200
    assert clear_ack.json()["incident"]["acknowledged_at"] is None

    clear_silence = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/silence",
        json={"clear": True},
        headers=owner_headers,
    )
    assert clear_silence.status_code == 200
    assert clear_silence.json()["incident"]["silenced_until"] is None

    reopen = client.post(
        f"/projects/{project_id}/incidents/{incident_id}/reopen",
        json={"reason": "The dependency regressed after deployment."},
        headers=owner_headers,
    )
    assert reopen.status_code == 200
    assert len(calls) == 3
    assert calls[2]["event_action"] == "trigger"
    assert calls[2]["custom_details"]["reopen_reason"] == "The dependency regressed after deployment."

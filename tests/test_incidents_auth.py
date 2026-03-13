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
        headers={"Authorization": "Bearer wrong-key"},
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
        headers={"Authorization": f"Bearer {api_key}"},
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
    assert public_payload["incident"]["id"] == incident_id
    assert len(public_payload["events"]) == 1

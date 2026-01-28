import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _seed_incidents(session):
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType, Incident
    from src.security import hash_api_key

    project = Project(name="simproj", api_key_hash=hash_api_key("simkey"))
    session.add(project)
    session.commit()
    session.refresh(project)

    check = Check(project_id=project.id, name="simcheck", type=CheckType.HTTP, status=CheckStatus.DOWN)
    session.add(check)
    session.commit()
    session.refresh(check)

    now = datetime.utcnow()
    inc1 = Incident(project_id=project.id, check_id=check.id, started_at=now - timedelta(days=1), status="open")
    inc2 = Incident(project_id=project.id, check_id=check.id, started_at=now, status="open")
    session.add_all([inc1, inc2])
    session.commit()
    session.refresh(inc1)
    session.refresh(inc2)

    msg = "gateway timeout on service api"
    e1 = Event(check_id=check.id, project_id=project.id, event_type=EventType.DOWN, message=msg, incident_id=inc1.id)
    e2 = Event(check_id=check.id, project_id=project.id, event_type=EventType.DOWN, message=msg, incident_id=inc2.id)
    session.add_all([e1, e2])
    session.commit()

    return project, inc1, inc2, msg


def test_find_similar_incidents(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'sim.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.analytics_ml import find_similar_incidents

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project, inc1, inc2, msg = _seed_incidents(session)
        matches = find_similar_incidents(
            session=session,
            project_id=project.id,
            target_text=msg,
            days=30,
            limit=5,
            threshold=0.1,
            target_incident_id=inc2.id,
        )
        assert any(m["incident_id"] == inc1.id for m in matches)


def test_incident_similarity_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'sim_api.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project, inc1, inc2, _ = _seed_incidents(session)
        project_id = project.id
        incident_id = inc2.id
        similar_id = inc1.id

    client = TestClient(app)
    headers = {"Authorization": "Bearer simkey"}
    resp = client.get(
        f"/projects/{project_id}/analytics/incident-similarity?incident_id={incident_id}&threshold=0.1",
        headers=headers,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["incident_id"] == incident_id
    assert any(m["incident_id"] == similar_id for m in payload.get("matches", []))

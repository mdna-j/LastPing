import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _seed_incidents(session, project_id, check_id):
    from src.models import Event, EventType, Incident

    now = datetime.utcnow()
    inc1 = Incident(project_id=project_id, check_id=check_id, started_at=now - timedelta(days=1), status="open")
    inc2 = Incident(project_id=project_id, check_id=check_id, started_at=now, status="open")
    session.add_all([inc1, inc2])
    session.commit()
    session.refresh(inc1)
    session.refresh(inc2)
    msg = "gateway timeout on service api"
    e1 = Event(check_id=check_id, project_id=project_id, event_type=EventType.DOWN, message=msg, incident_id=inc1.id)
    e2 = Event(check_id=check_id, project_id=project_id, event_type=EventType.DOWN, message=msg, incident_id=inc2.id)
    session.add_all([e1, e2])
    session.commit()
    return inc1, inc2


def test_incident_clusters_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'clusters.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="cproj", api_key_hash=hash_api_key("ckey"))
        session.add(project)
        session.commit()
        session.refresh(project)
        check = Check(project_id=project.id, name="c1", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)
        inc1, inc2 = _seed_incidents(session, project.id, check.id)
        pid = project.id
        inc1_id = inc1.id
        inc2_id = inc2.id

    client = TestClient(app)
    headers = {"Authorization": "Bearer ckey"}
    resp = client.get(f"/projects/{pid}/analytics/incident-clusters?threshold=0.1&min_cluster_size=2", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    clusters = data.get("clusters", [])
    assert any(inc1_id in c["incident_ids"] and inc2_id in c["incident_ids"] for c in clusters)


def test_anomaly_predictions_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'anoms.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()

    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    recent_hours = 6
    start = now - timedelta(hours=recent_hours)

    with Session(dbmod.engine) as session:
        project = Project(name="aproj", api_key_hash=hash_api_key("akey"))
        session.add(project)
        session.commit()
        session.refresh(project)
        check = Check(project_id=project.id, name="a1", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)
        for i in range(recent_hours):
            for j in range(i + 1):
                ev = Event(
                    check_id=check.id,
                    project_id=project.id,
                    event_type="down",
                    created_at=start + timedelta(hours=i, minutes=2, seconds=j),
                )
                session.add(ev)
        session.commit()
        pid = project.id
        cid = check.id

    client = TestClient(app)
    headers = {"Authorization": "Bearer akey"}
    resp = client.get(f"/projects/{pid}/analytics/anomalies?recent_hours={recent_hours}&z_threshold=0.5&min_events=1", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert any(w["check_id"] == cid for w in data.get("warnings", []))


def test_predictive_model_training_and_ml_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'ml_models.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()

    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=24)

    with Session(dbmod.engine) as session:
        project = Project(name="mproj", api_key_hash=hash_api_key("mkey"))
        session.add(project)
        session.commit()
        session.refresh(project)
        check = Check(project_id=project.id, name="m1", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        # seed 24 hours of events; heavier in the last 6 hours to induce a positive trend
        for i in range(24):
            count = 1 if i < 18 else (i - 17)
            for j in range(count):
                ev = Event(
                    check_id=check.id,
                    project_id=project.id,
                    event_type="down",
                    created_at=start + timedelta(hours=i, minutes=j),
                )
                session.add(ev)
        session.commit()
        pid = project.id
        cid = check.id

    client = TestClient(app)
    headers = {"Authorization": "Bearer mkey"}
    train = client.post(
        f"/projects/{pid}/analytics/predictive/train",
        json={"days": 1, "min_events": 1},
        headers=headers,
    )
    assert train.status_code == 200
    assert train.json().get("trained", 0) >= 1

    resp = client.get(
        f"/projects/{pid}/analytics/predictive?recent_hours=6&min_events=1&z_threshold=0.5",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("model_used") is True
    assert any(w["check_id"] == cid for w in data.get("warnings", []))

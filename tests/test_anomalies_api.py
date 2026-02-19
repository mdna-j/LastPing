import json
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_list_persisted_anomalies_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'anomalies.db'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.main import app
    from src.models import Project, Check, CheckType, CheckStatus, Incident, Anomaly
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    now = datetime.utcnow()

    with Session(dbmod.engine) as session:
        project = Project(name="anomproj", api_key_hash=hash_api_key("anomkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        other_project = Project(name="otherproj", api_key_hash=hash_api_key("otherkey"))
        session.add(other_project)
        session.commit()
        session.refresh(other_project)

        check = Check(project_id=project.id, name="api", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        other_check = Check(project_id=other_project.id, name="other", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(other_check)
        session.commit()
        session.refresh(other_check)

        inc = Incident(project_id=project.id, check_id=check.id, started_at=now - timedelta(minutes=10), status="open")
        session.add(inc)
        session.commit()
        session.refresh(inc)

        session.add(
            Anomaly(
                check_id=check.id,
                incident_id=inc.id,
                type="latency_spike",
                severity=0.93,
                window_start=now - timedelta(minutes=15),
                window_end=now - timedelta(minutes=5),
                evidence_json=json.dumps({"p95_ms": 820, "baseline_ms": 180}),
            )
        )
        session.add(
            Anomaly(
                check_id=other_check.id,
                incident_id=None,
                type="flapping",
                severity=0.70,
                window_start=now - timedelta(minutes=20),
                window_end=now - timedelta(minutes=10),
                evidence_json=json.dumps({"changes": 6}),
            )
        )
        session.commit()

        project_id = project.id
        check_id = check.id
        incident_id = inc.id

    client = TestClient(app)
    headers = {"Authorization": "Bearer anomkey"}

    resp = client.get(f"/projects/{project_id}/analytics/anomaly-events", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["anomalies"][0]["check_id"] == check_id
    assert payload["anomalies"][0]["incident_id"] == incident_id
    assert payload["anomalies"][0]["evidence"]["p95_ms"] == 820

    resp2 = client.get(
        f"/projects/{project_id}/analytics/anomaly-events?check_id={check_id}&incident_id={incident_id}",
        headers=headers,
    )
    assert resp2.status_code == 200
    payload2 = resp2.json()
    assert payload2["count"] == 1
    assert payload2["anomalies"][0]["type"] == "latency_spike"


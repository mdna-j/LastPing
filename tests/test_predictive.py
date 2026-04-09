import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_predictive_endpoint(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'pred.db'}"

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
        project = Project(name="predproj", api_key_hash=hash_api_key("predkey"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="predcheck", type=CheckType.HTTP, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        # Increasing event counts per hour to produce a positive trend.
        for i in range(recent_hours):
            for j in range(i + 1):
                ev = Event(
                    check_id=check.id,
                    project_id=project.id,
                    event_type="down",
                    created_at=start + timedelta(hours=i, minutes=1, seconds=j),
                )
                session.add(ev)
        session.commit()

        project_id = project.id
        check_id = check.id

    client = TestClient(app)
    headers = {"X-API-KEY": "predkey"}
    resp = client.get(
        f"/projects/{project_id}/analytics/predictive?recent_hours={recent_hours}&ratio_threshold=1.5&min_events=3",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(w["check_id"] == check_id for w in data.get("warnings", []))

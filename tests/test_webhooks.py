import os
from datetime import datetime

from fastapi.testclient import TestClient


def test_webhook_creates_check_and_event(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'test_webhooks.sqlite'}"
    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, Event
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        api_key = 'testkey'
        p = Project(name='p1', api_key_hash=hash_api_key(api_key))
        session.add(p)
        session.commit()
        session.refresh(p)

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"check_name": "webhook-check", "event": "down", "message": "failure via webhook"}
    r = client.post(f"/projects/{p.id}/webhook", json=payload, headers=headers)
    assert r.status_code in (200, 201, 202)

    # verify check and event
    with Session(dbmod.engine) as session:
        chk = session.exec(select(Check).where(Check.project_id == p.id, Check.name == 'webhook-check')).first()
        assert chk is not None
        ev = session.exec(select(Event).where(Event.project_id == p.id, Event.check_id == chk.id)).first()
        assert ev is not None


def test_webhook_heartbeat_updates_last_ping(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'test_webhooks2.sqlite'}"
    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        api_key = 'hbkey'
        p = Project(name='p2', api_key_hash=hash_api_key(api_key))
        session.add(p)
        session.commit()
        session.refresh(p)

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"check_name": "hb-check", "event": "heartbeat", "timestamp": datetime.utcnow().isoformat()}
    r = client.post(f"/projects/{p.id}/webhook", json=payload, headers=headers)
    assert r.status_code == 200

    with Session(dbmod.engine) as session:
        chk = session.exec(select(Check).where(Check.project_id == p.id, Check.name == 'hb-check')).first()
        assert chk is not None
        assert chk.last_ping is not None

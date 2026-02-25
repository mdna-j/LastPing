import os
from datetime import datetime, timedelta

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
        project_id = p.id

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"check_name": "webhook-check", "event": "down", "message": "failure via webhook"}
    r = client.post(f"/projects/{project_id}/webhook", json=payload, headers=headers)
    assert r.status_code in (200, 201, 202)

    # verify check and event
    with Session(dbmod.engine) as session:
        chk = session.exec(select(Check).where(Check.project_id == project_id, Check.name == 'webhook-check')).first()
        assert chk is not None
        ev = session.exec(select(Event).where(Event.project_id == project_id, Event.check_id == chk.id)).first()
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
        project_id = p.id

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"check_name": "hb-check", "event": "heartbeat", "timestamp": datetime.utcnow().isoformat()}
    r = client.post(f"/projects/{project_id}/webhook", json=payload, headers=headers)
    assert r.status_code == 200

    with Session(dbmod.engine) as session:
        chk = session.exec(select(Check).where(Check.project_id == project_id, Check.name == 'hb-check')).first()
        assert chk is not None
        assert chk.last_ping is not None


def test_webhook_ignores_stale_heartbeat_timestamp(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'test_webhooks3.sqlite'}"
    os.environ["HEARTBEAT_STALE_TOLERANCE_SECONDS"] = "30"
    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        api_key = 'stalehbkey'
        p = Project(name='p3', api_key_hash=hash_api_key(api_key))
        session.add(p)
        session.commit()
        session.refresh(p)
        project_id = p.id

        current = datetime.utcnow()
        chk = Check(
            project_id=p.id,
            name="hb-stale-check",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.DOWN,
            last_ping=current,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)
        check_id = chk.id
        expected_last_ping = chk.last_ping

    headers = {"Authorization": f"Bearer {api_key}"}
    stale_ts = (expected_last_ping - timedelta(minutes=5)).isoformat()
    payload = {"check_name": "hb-stale-check", "event": "heartbeat", "timestamp": stale_ts}
    r = client.post(f"/projects/{project_id}/webhook", json=payload, headers=headers)
    assert r.status_code == 200
    assert r.json().get("stale_ignored") is True

    with Session(dbmod.engine) as session:
        chk = session.get(Check, check_id)
        assert chk is not None
        assert chk.status == CheckStatus.DOWN
        assert chk.last_ping == expected_last_ping


def test_heartbeat_endpoint_ignores_stale_timestamp(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'test_webhooks4.sqlite'}"
    os.environ["HEARTBEAT_STALE_TOLERANCE_SECONDS"] = "30"
    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        api_key = 'stalehbdirect'
        p = Project(name='p4', api_key_hash=hash_api_key(api_key))
        session.add(p)
        session.commit()
        session.refresh(p)
        project_id = p.id

        current = datetime.utcnow()
        chk = Check(
            project_id=p.id,
            name="hb-direct-stale",
            type=CheckType.HEARTBEAT,
            status=CheckStatus.DOWN,
            last_ping=current,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)
        check_id = chk.id
        expected_last_ping = chk.last_ping

    headers = {"Authorization": f"Bearer {api_key}"}
    stale_ts = (expected_last_ping - timedelta(minutes=10)).isoformat()
    payload = {"timestamp": stale_ts}
    r = client.post(f"/projects/{project_id}/heartbeat/hb-direct-stale", json=payload, headers=headers)
    assert r.status_code == 200
    assert r.json().get("stale_ignored") is True

    with Session(dbmod.engine) as session:
        chk = session.get(Check, check_id)
        assert chk is not None
        assert chk.status == CheckStatus.DOWN
        assert chk.last_ping == expected_last_ping

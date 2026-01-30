import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_uptime_and_mttr_endpoints(tmp_path, monkeypatch):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'm.db'}"
    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, Event
    from src.main import app

    dbmod.create_db_and_tables()
    client = TestClient(app)

    now = datetime.utcnow()
    with Session(dbmod.engine) as s:
        from src.security import hash_api_key
        plain = 'testkey'
        p = Project(name='mproj', api_key_hash=hash_api_key(plain))
        s.add(p)
        s.commit()
        s.refresh(p)
        c = Check(project_id=p.id, name='mcheck')
        s.add(c)
        s.commit()
        s.refresh(c)

        # create events: down at t0, up at t0+60, down at t0+120, up at t0+200
        e1 = Event(check_id=c.id, project_id=p.id, event_type='down', created_at=now - timedelta(seconds=300))
        e2 = Event(check_id=c.id, project_id=p.id, event_type='up', created_at=now - timedelta(seconds=240))
        e3 = Event(check_id=c.id, project_id=p.id, event_type='down', created_at=now - timedelta(seconds=120))
        e4 = Event(check_id=c.id, project_id=p.id, event_type='up', created_at=now - timedelta(seconds=60))
        s.add_all([e1, e2, e3, e4])
        s.commit()
        p_id = p.id
        now_val = now

    # uptime over last 1 day
    headers = {"Authorization": f"Bearer {plain}"}
    r = client.get(f"/projects/{p_id}/metrics/uptime?start={(now_val - timedelta(days=1)).isoformat()}&end={(now_val).isoformat()}", headers=headers)
    assert r.status_code == 200
    j = r.json()
    assert 'uptime' in j
    assert 0.0 <= j['uptime'] <= 100.0

    # mttr over last 30 days
    r2 = client.get(f"/projects/{p_id}/metrics/mttr?start={(now_val - timedelta(days=30)).isoformat()}&end={(now_val).isoformat()}", headers=headers)
    assert r2.status_code == 200
    j2 = r2.json()
    assert 'mttr_seconds' in j2


def test_snapshots_endpoint(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'snap.db'}"
    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, UptimeSnapshot
    from src.main import app
    from fastapi.testclient import TestClient

    dbmod.create_db_and_tables()
    client = TestClient(app)

    now2 = datetime.utcnow()
    with Session(dbmod.engine) as s:
        from src.security import hash_api_key
        plain2 = 'snapkey'
        p = Project(name='sproj', api_key_hash=hash_api_key(plain2))
        s.add(p)
        s.commit()
        s.refresh(p)
        c = Check(project_id=p.id, name='scheck')
        s.add(c)
        s.commit()
        s.refresh(c)

        for i in range(3):
            snap = UptimeSnapshot(project_id=p.id, check_id=c.id, window_start=now2 - timedelta(hours=1+i), window_end=now2 - timedelta(hours=i), uptime_percent=99.0 - i, mttr_seconds=30 + i)
            s.add(snap)
        s.commit()
        p2_id = p.id

    headers2 = {"Authorization": f"Bearer {plain2}"}
    r = client.get(f"/projects/{p2_id}/metrics/snapshots", headers=headers2)
    assert r.status_code == 200
    arr = r.json()
    assert isinstance(arr, list)
    assert len(arr) == 3


def test_availability_rollup_monthly(tmp_path):
    os.environ['DATABASE_URL'] = f"sqlite:///{tmp_path / 'rollup.db'}"
    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, UptimeSnapshot
    from src.main import app
    from fastapi.testclient import TestClient

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as s:
        from src.security import hash_api_key
        plain = 'rollupkey'
        p = Project(name='rproj', api_key_hash=hash_api_key(plain), slo_target=99.0, sla_target=99.0)
        s.add(p)
        s.commit()
        s.refresh(p)
        c = Check(project_id=p.id, name='rcheck')
        s.add(c)
        s.commit()
        s.refresh(c)

        jan = datetime(2026, 1, 15, 12, 0, 0)
        feb = datetime(2026, 2, 15, 12, 0, 0)
        s.add(UptimeSnapshot(project_id=p.id, check_id=c.id, window_start=jan - timedelta(hours=24), window_end=jan, uptime_percent=98.0, mttr_seconds=10))
        s.add(UptimeSnapshot(project_id=p.id, check_id=c.id, window_start=feb - timedelta(hours=24), window_end=feb, uptime_percent=99.5, mttr_seconds=5))
        s.commit()
        pid = p.id

    headers = {"Authorization": f"Bearer {plain}"}
    end = datetime(2026, 3, 1, 0, 0, 0).isoformat()
    start = datetime(2025, 12, 1, 0, 0, 0).isoformat()
    r = client.get(f"/projects/{pid}/metrics/availability/rollup?period=month&start={start}&end={end}", headers=headers)
    assert r.status_code == 200
    data = r.json()
    series = data.get("series", [])
    assert any(row["period"].endswith("-01") for row in series)
    assert any(row["period"].endswith("-02") for row in series)

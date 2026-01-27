import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_grouping_and_merge_split(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_ig.sqlite'}"
    os.environ['ADMIN_TOKEN'] = 'admintoken'

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType, Incident
    from src import worker
    from src.security import hash_api_key
    from src.main import app

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        # create project and two checks
        project = Project(name="pg1", api_key_hash=hash_api_key("key1"))
        session.add(project)
        session.commit()
        session.refresh(project)

        chk1 = Check(project_id=project.id, name="c1", type=CheckType.HEARTBEAT, expected_interval=60, grace_period=10, last_ping=datetime.utcnow() - timedelta(hours=2), status=CheckStatus.UP)
        chk2 = Check(project_id=project.id, name="c2", type=CheckType.HEARTBEAT, expected_interval=60, grace_period=10, last_ping=datetime.utcnow() - timedelta(hours=2), status=CheckStatus.UP)
        session.add_all([chk1, chk2])
        session.commit()
        session.refresh(chk1)
        session.refresh(chk2)

        # create an open incident for chk1 that started recently (within group window)
        now = datetime.utcnow()
        inc1 = Incident(project_id=project.id, check_id=chk1.id, started_at=now - timedelta(seconds=100), status="open")
        session.add(inc1)
        session.commit()
        session.refresh(inc1)

        # Now run worker; chk2 should be marked DOWN and its incident should group with inc1
        monkeypatch.setattr(worker, 'notify_down', lambda *a, **k: True)
        worker.scan_checks_once(session)

        # check incident for chk2 is grouped under inc1
        chk2_inc = session.exec(select(Incident).where(Incident.check_id == chk2.id, Incident.resolved_at == None)).first()
        assert chk2_inc is not None
        assert chk2_inc.group_id == (inc1.group_id or inc1.id)

        # Create a second incident and some events to test merge/split via API
        inc2 = Incident(project_id=project.id, check_id=chk1.id, started_at=now - timedelta(seconds=50), status="open")
        session.add(inc2)
        session.commit()
        session.refresh(inc2)

        # add events under inc2
        e1 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.DOWN, message="down1", incident_id=inc2.id)
        e2 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.UP, message="up1", incident_id=inc2.id)
        session.add_all([e1, e2])
        session.commit()

        client = TestClient(app)

        # Merge inc2 into inc1
        headers = {"X-ADMIN-TOKEN": "admintoken"}
        resp = client.post(f"/projects/{project.id}/incidents/{inc2.id}/merge", json={"into": inc1.id}, headers=headers)
        assert resp.status_code == 200
        j = resp.json()
        assert j.get('merged') is True

        # events from inc2 should now point to inc1
        evs = session.exec(select(Event).where(Event.incident_id == inc1.id)).all()
        assert any(e.message == 'down1' for e in evs)

        # audit log row created for merge
        from src.models import AuditLog
        al_rows = session.exec(select(AuditLog).where(AuditLog.action == 'merge_incident', AuditLog.target_type == 'incident', AuditLog.target_id == inc2.id)).all()
        assert len(al_rows) == 1

        # Now test split: pick one event id to split out
        # create an incident with multiple events
        inc3 = Incident(project_id=project.id, check_id=chk1.id, started_at=now, status='open')
        session.add(inc3)
        session.commit()
        session.refresh(inc3)
        e3 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.DOWN, message='sdown', incident_id=inc3.id)
        e4 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.UP, message='sup', incident_id=inc3.id)
        session.add_all([e3, e4])
        session.commit()
        session.refresh(e3)
        session.refresh(e4)

        resp2 = client.post(f"/projects/{project.id}/incidents/{inc3.id}/split", json={"event_ids": [e3.id]}, headers=headers)
        assert resp2.status_code == 200
        j2 = resp2.json()
        assert 'split_into' in j2
        new_id = j2['split_into']
        # the moved event should now reference the new incident
        # expire session state to ensure fresh reads from DB (other session committed changes)
        session.expire_all()
        moved = session.exec(select(Event).where(Event.id == e3.id)).first()
        assert moved.incident_id == new_id
        # audit log row created for split
        al2 = session.exec(select(AuditLog).where(AuditLog.action == 'split_incident', AuditLog.target_type == 'incident', AuditLog.target_id == inc3.id)).all()
        assert len(al2) == 1

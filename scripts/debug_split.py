"""Debug script to reproduce split behavior and print API response and DB rows."""
from datetime import datetime, timedelta
import os
from sqlmodel import Session, select
import sys

# ensure project root is on path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['DATABASE_URL'] = 'sqlite:///./tmp_debug_split.db'
from src import db as dbmod
from src.models import Project, Check, CheckType, CheckStatus, Event, EventType, Incident
from src.security import hash_api_key
from src.main import app
from fastapi.testclient import TestClient

# recreate DB
try:
    import pathlib
    p = pathlib.Path('./tmp_debug_split.db')
    if p.exists():
        p.unlink()
except Exception:
    pass

dbmod.create_db_and_tables()

with Session(dbmod.engine) as session:
    project = Project(name='pgd', api_key_hash=hash_api_key('key1'))
    session.add(project)
    session.commit()
    session.refresh(project)

    chk1 = Check(project_id=project.id, name='c1', type=CheckType.HEARTBEAT, expected_interval=60, grace_period=10, last_ping=datetime.utcnow()-timedelta(hours=2), status=CheckStatus.UP)
    session.add(chk1)
    session.commit()
    session.refresh(chk1)

    inc3 = Incident(project_id=project.id, check_id=chk1.id, started_at=datetime.utcnow(), status='open')
    session.add(inc3)
    session.commit()
    session.refresh(inc3)

    e3 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.DOWN, message='sdown', incident_id=inc3.id)
    e4 = Event(check_id=chk1.id, project_id=project.id, event_type=EventType.UP, message='sup', incident_id=inc3.id)
    session.add_all([e3, e4])
    session.commit()
    session.refresh(e3)
    session.refresh(e4)

    client = TestClient(app)
    headers = {'Authorization': 'Bearer key1'}
    resp = client.post(f'/projects/{project.id}/incidents/{inc3.id}/split', json={'event_ids': [e3.id]}, headers=headers)
    print('status', resp.status_code)
    print('json', resp.json())

    # open a fresh session to examine DB
with Session(dbmod.engine) as s2:
    ev = s2.exec(select(Event).where(Event.id == e3.id)).first()
    print('db event incident_id:', ev.incident_id)

print('done')

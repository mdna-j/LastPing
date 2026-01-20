import os
import sys
from sqlmodel import Session, select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ['DATABASE_URL'] = 'sqlite:///./tmp_debug_webhook.db'
from src import db as dbmod
from src.models import Project, Check, Event
from src.security import hash_api_key
from fastapi.testclient import TestClient
from src.main import app

# setup
if os.path.exists('./tmp_debug_webhook.db'):
    os.remove('./tmp_debug_webhook.db')
dbmod.create_db_and_tables()
client = TestClient(app)

# inspect route bindings
print('Registered routes:')
for r in app.router.routes:
    try:
        ep = getattr(r, 'endpoint', None)
        print(r.path, '->', ep, getattr(r, 'name', None), 'module=', getattr(ep, '__module__', None))
    except Exception:
        print('route', r)

with Session(dbmod.engine) as s:
    key = 'dbgkey'
    p = Project(name='dbg', api_key_hash=hash_api_key(key))
    s.add(p)
    s.commit()
    s.refresh(p)
    pid = p.id

headers = {"Authorization": f"Bearer {key}"}
res = client.post(f"/projects/{pid}/webhook", json={"check_name":"dbg-check","event":"down","message":"dbg"}, headers=headers)
print('status', res.status_code, 'body', end=' ')
try:
    print(res.json())
except Exception as e:
    print('<non-json response>', e)

with Session(dbmod.engine) as s:
    ch = s.exec(select(Check).where(Check.project_id==pid)).all()
    ev = s.exec(select(Event).where(Event.project_id==pid)).all()
    print('checks', ch)
    print('events', ev)

print('text:', repr(res.text))
print('content:', res.content)
print('headers:', dict(res.headers))
import os
import sys
from sqlmodel import Session, select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ['DATABASE_URL'] = 'sqlite:///./tmp_debug_webhook.db'
from src import db as dbmod
from src.models import Project, Check, Event
from src.security import hash_api_key
from fastapi.testclient import TestClient
from src.main import app

# setup
if os.path.exists('./tmp_debug_webhook.db'):
    os.remove('./tmp_debug_webhook.db')
dbmod.create_db_and_tables()
client = TestClient(app)

with Session(dbmod.engine) as s:
    key = 'dbgkey'
    p = Project(name='dbg', api_key_hash=hash_api_key(key))
    s.add(p)
    s.commit()
    s.refresh(p)
    pid = p.id

headers = {"Authorization": f"Bearer {key}"}
res = client.post(f"/projects/{pid}/webhook", json={"check_name":"dbg-check","event":"down","message":"dbg"}, headers=headers)
print('status', res.status_code, 'body', end=' ')
try:
    print(res.json())
except Exception as e:
    print('<non-json response>', e)

with Session(dbmod.engine) as s:
    ch = s.exec(select(Check).where(Check.project_id==pid)).all()
    ev = s.exec(select(Event).where(Event.project_id==pid)).all()
    print('checks', ch)
    print('events', ev)

print('text:', repr(res.text))
print('content:', res.content)
print('headers:', dict(res.headers))

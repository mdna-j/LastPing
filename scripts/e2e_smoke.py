"""Lightweight E2E smoke script exercising incident merge/split flows.

This uses the FastAPI TestClient and operates on the local SQLite dev DB.
Run from repository root: `python scripts/e2e_smoke.py`
"""
import os
from fastapi.testclient import TestClient
from sqlmodel import Session

from src.main import app
from src import db as dbmod
from src.models import Project, Check as CheckModel, Incident, Event

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "e2e-admin-token")


def prepare_db():
    # ensure tables exist
    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as s:
        # create a project and a check
        p = Project(name="e2e-project")
        s.add(p)
        s.commit()
        s.refresh(p)
        c = CheckModel(project_id=p.id, name="e2e-check")
        s.add(c)
        s.commit()
        s.refresh(c)

        # create a source incident with two events
        src = Incident(project_id=p.id, check_id=c.id)
        s.add(src)
        s.commit()
        s.refresh(src)
        e1 = Event(check_id=c.id, project_id=p.id, event_type="down", message="first", incident_id=src.id)
        e2 = Event(check_id=c.id, project_id=p.id, event_type="down", message="second", incident_id=src.id)
        s.add(e1)
        s.add(e2)
        s.commit()
        s.refresh(e1)
        s.refresh(e2)

        # create a target incident for merge
        tgt = Incident(project_id=p.id, check_id=c.id)
        s.add(tgt)
        s.commit()
        s.refresh(tgt)

        # return primitive IDs to avoid DetachedInstanceError outside the session
        return {
            "project_id": p.id,
            "check_id": c.id,
            "src_id": src.id,
            "tgt_id": tgt.id,
            "event_ids": [e1.id, e2.id],
        }


def run_smoke():
    os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
    prepare = prepare_db()
    client = TestClient(app)

    # Merge src -> tgt
    merge_payload = {"into": prepare["tgt_id"]}
    headers = {"X-ADMIN-TOKEN": ADMIN_TOKEN}
    r = client.post(f"/projects/{prepare['project_id']}/incidents/{prepare['src_id']}/merge", json=merge_payload, headers=headers)
    assert r.status_code == 200, f"merge failed: {r.status_code} {r.text}"
    print("merge response:", r.json())

    # Prepare a new incident to split: create an incident with three events
    with Session(dbmod.engine) as s:
        new_src = Incident(project_id=prepare['project_id'], check_id=prepare['check_id'])
        s.add(new_src)
        s.commit()
        s.refresh(new_src)
        ev_ids = []
        for i in range(3):
            ev = Event(check_id=prepare['check_id'], project_id=prepare['project_id'], event_type='down', message=f'ev{i}', incident_id=new_src.id)
            s.add(ev)
            s.commit()
            s.refresh(ev)
            ev_ids.append(ev.id)
        new_src_id = new_src.id
    # split off the first two events
    ev_ids = [ev_ids[0], ev_ids[1]]
    split_payload = {"event_ids": ev_ids}
    r2 = client.post(f"/projects/{prepare['project_id']}/incidents/{new_src_id}/split", json=split_payload, headers=headers)
    assert r2.status_code == 200, f"split failed: {r2.status_code} {r2.text}"
    print("split response:", r2.json())

    print("E2E smoke: OK")


if __name__ == '__main__':
    run_smoke()

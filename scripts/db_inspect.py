"""Inspect dev DB for incidents and events related to e2e smoke run.
Run: python scripts/db_inspect.py
"""
from sqlmodel import Session, select
from src import db as dbmod
from src.models import Project, Incident, Event


def main():
    dbmod.create_db_and_tables()
    with Session(dbmod.engine) as s:
        proj = s.exec(select(Project).where(Project.name == "e2e-project")).first()
        if not proj:
            print("No project named 'e2e-project' found.")
            return
        print(f"Project: id={proj.id} name={proj.name}")
        incs = s.exec(select(Incident).where(Incident.project_id == proj.id).order_by(Incident.id)).all()
        print("Incidents:")
        for i in incs:
            print(f"  id={i.id} check_id={i.check_id} status={i.status} started_at={i.started_at} resolved_at={i.resolved_at} group_id={getattr(i,'group_id',None)} merged_into={getattr(i,'merged_into',None)}")
        evs = s.exec(select(Event).where(Event.project_id == proj.id).order_by(Event.id)).all()
        print("Events:")
        for e in evs:
            print(f"  id={e.id} check_id={e.check_id} incident_id={e.incident_id} type={e.event_type} message={e.message} created_at={e.created_at}")

if __name__ == '__main__':
    main()

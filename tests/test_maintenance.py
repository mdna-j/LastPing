from datetime import datetime, timedelta
import os


def test_check_level_maintenance_suppresses_alert(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mdb1.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(name="mproj1", api_key_hash=hash_api_key("k"))
        session.add(project)
        session.commit()
        session.refresh(project)

        old = datetime.utcnow() - timedelta(hours=2)
        # set check-level maintenance covering now
        check = Check(
            project_id=project.id,
            name="chk_m1",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=old,
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
            maintenance_starts_at=datetime.utcnow() - timedelta(minutes=5),
            maintenance_ends_at=datetime.utcnow() + timedelta(minutes=5),
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        calls = {'n': 0}

        def fake_notify_down(chk, proj, reason=None):
            calls['n'] += 1

        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        worker.scan_checks_once(session)

        assert calls['n'] == 0
        events = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any('suppressed due to maintenance' in (e.message or '') for e in events)


def test_project_level_maintenance_suppresses_alert(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mdb2.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(
            name="mproj2",
            api_key_hash=hash_api_key("k2"),
            maintenance_starts_at=datetime.utcnow() - timedelta(minutes=5),
            maintenance_ends_at=datetime.utcnow() + timedelta(minutes=5),
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        old = datetime.utcnow() - timedelta(hours=2)
        check = Check(
            project_id=project.id,
            name="chk_m2",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=old,
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        calls = {'n': 0}

        def fake_notify_down(chk, proj, reason=None):
            calls['n'] += 1

        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        worker.scan_checks_once(session)

        assert calls['n'] == 0
        events = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any('suppressed due to maintenance' in (e.message or '') for e in events)

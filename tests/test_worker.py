import os
from datetime import datetime, timedelta

import pytest


def test_worker_marks_overdue_heartbeat(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db.sqlite'}"

    # import after setting DATABASE_URL so engine is created with test DB
    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(name="proj1", api_key_hash=hash_api_key("key"))
        session.add(project)
        session.commit()
        session.refresh(project)

        # create a heartbeat check with last_ping far in the past
        old = datetime.utcnow() - timedelta(hours=2)
        check = Check(
            project_id=project.id,
            name="hb1",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=old,
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        called = {}

        def fake_notify_down(chk, proj, reason=None):
            called['down'] = (chk.id, reason)

        # replace notify_down to avoid network calls
        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        # run one scan
        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.status == CheckStatus.DOWN

        events = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any(e.event_type == EventType.DOWN for e in events)
        assert 'down' in called


def test_worker_http_check_failure(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db2.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(name="proj2", api_key_hash=hash_api_key("key2"))
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http1",
            type=CheckType.HTTP,
            url="http://example.invalid/health",
            timeout=1,
            retries=1,
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        # force urlopen to raise to simulate failure
        def fake_urlopen(*args, **kwargs):
            raise Exception("conn refused")

        monkeypatch.setattr(worker.urllib.request, 'urlopen', fake_urlopen)

        called = {}

        def fake_notify_down(chk, proj, reason=None):
            called['down'] = reason

        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.status == CheckStatus.DOWN

        events = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any(e.event_type in (EventType.HTTP_FAILURE, EventType.DOWN) for e in events)
        assert 'down' in called
        assert 'conn refused' in called['down']


def test_worker_recovery_transitions(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db3.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(name="proj3", api_key_hash=hash_api_key("key3"))
        session.add(project)
        session.commit()
        session.refresh(project)

        # Heartbeat recovery: status currently DOWN but last_ping is recent
        recent = datetime.utcnow()
        hb = Check(
            project_id=project.id,
            name="hb_recover",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=recent,
            status=CheckStatus.DOWN,
        )
        session.add(hb)

        # HTTP recovery: status DOWN but url responds 200
        httpc = Check(
            project_id=project.id,
            name="http_recover",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            timeout=1,
            retries=1,
            status=CheckStatus.DOWN,
        )
        session.add(httpc)
        session.commit()
        session.refresh(hb)
        session.refresh(httpc)

        # monkeypatch urlopen to return a 200 response
        class FakeResp:
            def __init__(self, code=200):
                self._code = code

            def getcode(self):
                return self._code

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(*args, **kwargs):
            return FakeResp(200)

        monkeypatch.setattr(worker.urllib.request, 'urlopen', fake_urlopen)

        called = {}

        def fake_notify_recovery(chk, proj):
            called.setdefault('recovery', []).append(chk.name)

        monkeypatch.setattr(worker, 'notify_recovery', fake_notify_recovery)

        worker.scan_checks_once(session)

        session.refresh(hb)
        session.refresh(httpc)

        assert hb.status == CheckStatus.UP
        assert httpc.status == CheckStatus.UP

        events_hb = session.exec(select(Event).where(Event.check_id == hb.id)).all()
        events_http = session.exec(select(Event).where(Event.check_id == httpc.id)).all()
        assert any(e.event_type == EventType.UP for e in events_hb)
        assert any(e.event_type == EventType.UP for e in events_http)
        assert 'recovery' in called


def test_alert_suppression(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db4.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        from src.security import hash_api_key
        project = Project(name="proj4", api_key_hash=hash_api_key("key4"))
        session.add(project)
        session.commit()
        session.refresh(project)

        old = datetime.utcnow() - timedelta(hours=2)
        check = Check(
            project_id=project.id,
            name="hb_supp",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=old,
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
            alert_cooldown=3600,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        calls = {'n': 0}

        def fake_notify_down(chk, proj, reason=None):
            calls['n'] += 1

        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        # run scan twice quickly; only one alert should be sent due to cooldown
        worker.scan_checks_once(session)
        worker.scan_checks_once(session)

        assert calls['n'] == 1


def test_realert_still_down(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_realert.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_realert")
        session.add(project)
        session.commit()
        session.refresh(project)

        old = datetime.utcnow() - timedelta(hours=2)
        check = Check(
            project_id=project.id,
            name="hb_realert",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=old,
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
            alert_cooldown=0,
        )
        session.add(check)
        session.commit()

        calls = []

        def fake_notify_down(chk, proj, reason=None):
            calls.append(reason)

        monkeypatch.setattr(worker, 'notify_down', fake_notify_down)

        worker.scan_checks_once(session)
        worker.scan_checks_once(session)

        assert len(calls) >= 2
        assert any("still down" in (r or "") for r in calls)


def test_http_check_scheduling(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db5.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_sched", api_key="ksched")
        session.add(project)
        session.commit()
        session.refresh(project)

        now = datetime.utcnow()

        # create an HTTP check with next_run in the future -> should be skipped
        check = Check(
            project_id=project.id,
            name="http_sched",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            timeout=1,
            retries=1,
            status=CheckStatus.UP,
            interval=60,
            next_run=now + timedelta(seconds=300),
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        # monkeypatch urlopen to raise if called (should not be called)
        def fail_if_called(*args, **kwargs):
            raise AssertionError("HTTP check should not have been executed before next_run")

        monkeypatch.setattr(worker.urllib.request, 'urlopen', fail_if_called)

        # scan should skip the check
        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.last_ping is None

        # now set next_run to past and monkeypatch urlopen to return 200
        class FakeResp:
            def __init__(self, code=200):
                self._code = code

            def getcode(self):
                return self._code

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(*args, **kwargs):
            return FakeResp(200)

        check.next_run = now - timedelta(seconds=1)
        session.add(check)
        session.commit()

        monkeypatch.setattr(worker.urllib.request, 'urlopen', fake_urlopen)

        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.last_ping is not None
        assert check.next_run is not None and check.next_run > datetime.utcnow()


def test_degraded_latency_transition(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_deg.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_deg")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_deg",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            timeout=1,
            retries=1,
            status=CheckStatus.UP,
            latency_threshold_ms=10,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (True, "status=200", 55.0))
        monkeypatch.setattr(worker, "notify_degraded", lambda chk, proj, reason=None: None)

        worker.scan_checks_once(session)

        session.refresh(check)
        assert check.status == CheckStatus.DEGRADED
        events = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any(e.event_type == EventType.DEGRADED for e in events)


def test_tcp_and_dns_checks(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_tcp_dns.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_tcp_dns")
        session.add(project)
        session.commit()
        session.refresh(project)

        tcp_check = Check(project_id=project.id, name="tcp1", type=CheckType.TCP, host="example.com", port=443, status=CheckStatus.UP)
        dns_check = Check(project_id=project.id, name="dns1", type=CheckType.DNS, host="example.com", dns_record_type="A", status=CheckStatus.UP)
        session.add(tcp_check)
        session.add(dns_check)
        session.commit()

        monkeypatch.setattr(worker, "_tcp_check", lambda host, port, timeout: (True, "tcp_ok", 10.0))
        monkeypatch.setattr(worker, "_dns_check", lambda host, record_type=None: (True, "dns_ok", 5.0))

        worker.scan_checks_once(session)

        session.refresh(tcp_check)
        session.refresh(dns_check)
        assert tcp_check.last_ping is not None
        assert dns_check.last_ping is not None


def test_region_filtering(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_region.sqlite'}"
    os.environ["WORKER_REGION"] = "us-east"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_region")
        session.add(project)
        session.commit()
        session.refresh(project)

        skip_check = Check(project_id=project.id, name="http_skip", type=CheckType.HTTP, url="http://example.ok/health", region="eu-west", status=CheckStatus.UP)
        session.add(skip_check)
        session.commit()

        def fail_if_called(*args, **kwargs):
            raise AssertionError("Check should have been skipped for region mismatch")

        monkeypatch.setattr(worker, "_http_check", fail_if_called)

        worker.scan_checks_once(session)

        session.refresh(skip_check)
        assert skip_check.last_ping is None

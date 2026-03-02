import os
from datetime import datetime, timedelta, timezone

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


def test_check_result_stores_canonical_evidence_fields(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_evidence.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckResult
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_evidence")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_evidence",
            type=CheckType.HTTP,
            url="http://example.invalid/health",
            timeout=1,
            retries=1,
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (False, "timeout", None))
        monkeypatch.setattr(worker, "notify_down", lambda chk, proj, reason=None: None)

        worker.scan_checks_once(session)

        first = session.exec(
            select(CheckResult).where(CheckResult.check_id == check.id).order_by(CheckResult.id.desc())
        ).first()
        assert first is not None
        assert first.created_at is not None
        assert first.check_id == check.id
        assert first.project_id == project.id
        assert first.status == CheckStatus.DOWN
        assert first.latency_ms is None
        assert "timeout" in (first.error_message or "")
        assert first.incident_id is not None

        check.next_run = datetime.utcnow() - timedelta(seconds=1)
        session.add(check)
        session.commit()

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (True, "status=200", 12.5))

        worker.scan_checks_once(session)

        second = session.exec(
            select(CheckResult).where(CheckResult.check_id == check.id).order_by(CheckResult.id.desc())
        ).first()
        assert second is not None
        assert second.status == CheckStatus.UP
        assert second.latency_ms == pytest.approx(12.5)
        assert second.error_message is None


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


def test_tcp_dns_scheduling(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_tcp_dns_sched.sqlite'}"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_tcp_dns_sched")
        session.add(project)
        session.commit()
        session.refresh(project)

        now = datetime.utcnow()
        tcp_check = Check(project_id=project.id, name="tcp_sched", type=CheckType.TCP, host="example.com", port=443, status=CheckStatus.UP, interval=60, next_run=now + timedelta(seconds=300))
        dns_check = Check(project_id=project.id, name="dns_sched", type=CheckType.DNS, host="example.com", dns_record_type="A", status=CheckStatus.UP, interval=60, next_run=now + timedelta(seconds=300))
        session.add(tcp_check)
        session.add(dns_check)
        session.commit()

        monkeypatch.setattr(worker, "_tcp_check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("TCP should be skipped")))
        monkeypatch.setattr(worker, "_dns_check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("DNS should be skipped")))

        worker.scan_checks_once(session)
        session.refresh(tcp_check)
        session.refresh(dns_check)
        assert tcp_check.last_ping is None
        assert dns_check.last_ping is None

        tcp_check.next_run = datetime.utcnow() - timedelta(seconds=1)
        dns_check.next_run = datetime.utcnow() - timedelta(seconds=1)
        session.add(tcp_check)
        session.add(dns_check)
        session.commit()

        monkeypatch.setattr(worker, "_tcp_check", lambda host, port, timeout: (True, "tcp_ok", 12.0))
        monkeypatch.setattr(worker, "_dns_check", lambda host, record_type=None: (True, "dns_ok", 8.0))

        worker.scan_checks_once(session)
        session.refresh(tcp_check)
        session.refresh(dns_check)
        assert tcp_check.last_ping is not None
        assert dns_check.last_ping is not None
        assert tcp_check.next_run is not None
        assert dns_check.next_run is not None


def test_tcp_dns_failure(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_tcp_dns_fail.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_tcp_dns_fail")
        session.add(project)
        session.commit()
        session.refresh(project)

        tcp_check = Check(project_id=project.id, name="tcp_fail", type=CheckType.TCP, host="example.com", port=443, status=CheckStatus.UP, alert_enabled=True, alert_after=1, alert_cooldown=0)
        dns_check = Check(project_id=project.id, name="dns_fail", type=CheckType.DNS, host="example.com", dns_record_type="A", status=CheckStatus.UP, alert_enabled=True, alert_after=1, alert_cooldown=0)
        session.add(tcp_check)
        session.add(dns_check)
        session.commit()
        session.refresh(tcp_check)
        session.refresh(dns_check)

        monkeypatch.setattr(worker, "_tcp_check", lambda host, port, timeout: (False, "tcp_timeout", None))
        monkeypatch.setattr(worker, "_dns_check", lambda host, record_type=None: (False, "dns_nxdomain", None))

        called = {"reasons": []}

        def fake_notify_down(chk, proj, reason=None):
            called["reasons"].append(reason)

        monkeypatch.setattr(worker, "notify_down", fake_notify_down)

        worker.scan_checks_once(session)
        session.refresh(tcp_check)
        session.refresh(dns_check)
        assert tcp_check.status == CheckStatus.DOWN
        assert dns_check.status == CheckStatus.DOWN
        evs = session.exec(select(Event).where(Event.project_id == project.id)).all()
        assert any(e.event_type == EventType.DOWN for e in evs)
        assert any("tcp_timeout" in (r or "") for r in called["reasons"])
        assert any("dns_nxdomain" in (r or "") for r in called["reasons"])


def test_script_check_success(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_script_ok.sqlite'}"
    os.environ["CUSTOM_CHECKS_DIR"] = str(tmp_path / "custom_checks")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    custom_dir = tmp_path / "custom_checks"
    custom_dir.mkdir(parents=True, exist_ok=True)
    script = custom_dir / "ok.py"
    script.write_text("import sys\nprint('ok')\nsys.exit(0)\n", encoding="utf-8")

    with Session(dbmod.engine) as session:
        project = Project(name="proj_script_ok")
        session.add(project)
        session.commit()
        session.refresh(project)

        chk = Check(
            project_id=project.id,
            name="script_ok",
            type=CheckType.SCRIPT,
            script_path="ok.py",
            interval=60,
            timeout=2,
            retries=1,
            status=CheckStatus.UP,
            alert_enabled=False,
        )
        session.add(chk)
        session.commit()

        worker.scan_checks_once(session)

        session.refresh(chk)
        assert chk.last_ping is not None
        assert chk.last_latency_ms is not None
        assert chk.next_run is not None
        assert chk.status in (CheckStatus.UP, CheckStatus.DEGRADED)


def test_script_scheduling_skips(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_script_skip.sqlite'}"
    os.environ["CUSTOM_CHECKS_DIR"] = str(tmp_path / "custom_checks2")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    custom_dir = tmp_path / "custom_checks2"
    custom_dir.mkdir(parents=True, exist_ok=True)
    script = custom_dir / "ok.py"
    script.write_text("import sys\nprint('ok')\nsys.exit(0)\n", encoding="utf-8")

    with Session(dbmod.engine) as session:
        project = Project(name="proj_script_skip")
        session.add(project)
        session.commit()
        session.refresh(project)

        now = datetime.utcnow()
        chk = Check(
            project_id=project.id,
            name="script_skip",
            type=CheckType.SCRIPT,
            script_path="ok.py",
            interval=60,
            next_run=now + timedelta(seconds=300),
            timeout=2,
            retries=1,
            status=CheckStatus.UP,
        )
        session.add(chk)
        session.commit()

        monkeypatch.setattr(worker, "_script_check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Script should be skipped")))
        worker.scan_checks_once(session)
        session.refresh(chk)
        assert chk.last_ping is None


def test_script_check_failure(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_script_fail.sqlite'}"
    os.environ["CUSTOM_CHECKS_DIR"] = str(tmp_path / "custom_checks3")

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    custom_dir = tmp_path / "custom_checks3"
    custom_dir.mkdir(parents=True, exist_ok=True)
    script = custom_dir / "fail.py"
    script.write_text("import sys\nprint('nope')\nsys.exit(2)\n", encoding="utf-8")

    with Session(dbmod.engine) as session:
        project = Project(name="proj_script_fail")
        session.add(project)
        session.commit()
        session.refresh(project)

        chk = Check(
            project_id=project.id,
            name="script_fail",
            type=CheckType.SCRIPT,
            script_path="fail.py",
            interval=60,
            timeout=2,
            retries=1,
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
            alert_cooldown=0,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)

        called = {}

        def fake_notify_down(chk2, proj, reason=None):
            called["reason"] = reason

        monkeypatch.setattr(worker, "notify_down", fake_notify_down)

        worker.scan_checks_once(session)
        session.refresh(chk)
        assert chk.status == CheckStatus.DOWN
        evs = session.exec(select(Event).where(Event.check_id == chk.id)).all()
        assert any(e.event_type == EventType.DOWN for e in evs)
        assert "reason" in called
        assert "exit=2" in (called["reason"] or "")


def test_check_schema_requires_script_path_for_script_type():
    from src.routers.checks import CheckCreate

    with pytest.raises(Exception):
        CheckCreate(name="x", type="script")


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


def test_region_list_allows(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_region_list.sqlite'}"
    os.environ["WORKER_REGION"] = "us-east"

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_region_list")
        session.add(project)
        session.commit()
        session.refresh(project)

        allow_check = Check(project_id=project.id, name="http_allow", type=CheckType.HTTP, url="http://example.ok/health", region="us-east, eu-west", status=CheckStatus.UP)
        session.add(allow_check)
        session.commit()

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (True, "status=200", 5.0))

        worker.scan_checks_once(session)
        session.refresh(allow_check)
        assert allow_check.last_ping is not None


def test_region_failover_requires_expired_lease(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_region_failover.sqlite'}"
    monkeypatch.setenv("WORKER_REGION_FAILOVER", "1")
    monkeypatch.setenv("WORKER_FAILOVER_AFTER_SECONDS", "300")
    monkeypatch.setenv("WORKER_CLOCK_SKEW_TOLERANCE_SECONDS", "0")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckLease
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_region_failover")
        session.add(project)
        session.commit()
        session.refresh(project)

        chk = Check(
            project_id=project.id,
            name="http_region_failover",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            region="us-east",
            status=CheckStatus.UP,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)

        now = datetime.utcnow()
        # Without a lease, non-matching regions should not be allowed.
        assert worker._allow_region_or_failover(session, "eu-west", chk, now) is False

        # With a lease that is not expired beyond grace, still not allowed.
        lease = CheckLease(check_id=chk.id, lease_owner="us-east-1", lease_expires_at=now + timedelta(seconds=60), updated_at=now)
        session.add(lease)
        session.commit()
        assert worker._allow_region_or_failover(session, "eu-west", chk, now) is False

        # When the lease is expired beyond grace, failover is allowed.
        lease.lease_expires_at = now - timedelta(seconds=301)
        session.add(lease)
        session.commit()
        assert worker._allow_region_or_failover(session, "eu-west", chk, now) is True


def test_region_failover_handles_mixed_timezone_datetimes(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_region_failover_tz.sqlite'}"
    monkeypatch.setenv("WORKER_REGION_FAILOVER", "1")
    monkeypatch.setenv("WORKER_FAILOVER_AFTER_SECONDS", "300")
    monkeypatch.setenv("WORKER_CLOCK_SKEW_TOLERANCE_SECONDS", "0")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckLease
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_region_failover_tz")
        session.add(project)
        session.commit()
        session.refresh(project)

        chk = Check(
            project_id=project.id,
            name="http_region_failover_tz",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            region="us-east",
            status=CheckStatus.UP,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)

        now = datetime.utcnow()
        lease = CheckLease(
            check_id=chk.id,
            lease_owner="us-east-1",
            lease_expires_at=now - timedelta(seconds=301),  # naive
            updated_at=now,
        )
        session.add(lease)
        session.commit()

        # Simulate DB adapters returning aware timestamps (e.g. Postgres timezone-aware now()).
        monkeypatch.setattr(worker, "_db_now", lambda _session: datetime.now(timezone.utc))
        assert worker._allow_region_or_failover(session, "eu-west", chk, now) is True


def test_region_failback_cooldown_blocks_primary_reclaim(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_region_failback_cooldown.sqlite'}"
    monkeypatch.setenv("WORKER_REGION_FAILOVER", "1")
    monkeypatch.setenv("WORKER_FAILBACK_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("WORKER_REGION", "us-east")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckLease
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_region_failback_cooldown")
        session.add(project)
        session.commit()
        session.refresh(project)

        chk = Check(
            project_id=project.id,
            name="http_region_failback_cooldown",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            region="us-east",
            status=CheckStatus.UP,
        )
        session.add(chk)
        session.commit()
        session.refresh(chk)

        now = datetime.utcnow()
        monkeypatch.setattr(worker, "_db_now", lambda _session: now)

        lease = CheckLease(
            check_id=chk.id,
            lease_owner="eu-west-worker-1",
            lease_expires_at=now + timedelta(seconds=60),
            updated_at=now - timedelta(seconds=30),
            lease_fence=4,
        )
        session.add(lease)
        session.commit()

        # Primary region should honor failback cooldown while failover owner is recent.
        assert worker._allow_region_or_failover(session, "us-east", chk, now) is False

        lease.updated_at = now - timedelta(seconds=121)
        session.add(lease)
        session.commit()

        # After cooldown window, primary region can resume ownership attempts.
        assert worker._allow_region_or_failover(session, "us-east", chk, now) is True


def test_degraded_recovery(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_deg_recover.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_deg_recover")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_deg_recover",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            timeout=1,
            retries=1,
            status=CheckStatus.UP,
            latency_threshold_ms=10,
            alert_enabled=True,
            alert_cooldown=0,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (True, "status=200", 55.0))
        monkeypatch.setattr(worker, "notify_degraded", lambda chk, proj, reason=None: None)

        worker.scan_checks_once(session)
        session.refresh(check)
        assert check.status == CheckStatus.DEGRADED

        # allow next run immediately
        check.next_run = datetime.utcnow() - timedelta(seconds=1)
        session.add(check)
        session.commit()

        called = {"recovery": 0}

        def fake_recovery(chk, proj):
            called["recovery"] += 1

        monkeypatch.setattr(worker, "_http_check", lambda url, timeout, retries: (True, "status=200", 5.0))
        monkeypatch.setattr(worker, "notify_recovery", fake_recovery)

        worker.scan_checks_once(session)
        session.refresh(check)
        assert check.status == CheckStatus.UP
        evs = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert any(e.event_type == EventType.UP for e in evs)
        assert called["recovery"] >= 1


def test_lease_fencing_token_increments_on_acquire(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_lease_fence.sqlite'}"
    monkeypatch.setenv("WORKER_LEASES", "1")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("WORKER_ID", "worker-test-1")

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckLease
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_lease_fence")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_lease",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        now = datetime.utcnow()
        first_fence = worker._acquire_lease(session, check, now)
        assert first_fence == 1
        lease = session.get(CheckLease, check.id)
        assert lease is not None
        assert lease.lease_owner == "worker-test-1"
        assert lease.lease_fence == 1

        second_fence = worker._acquire_lease(session, check, now + timedelta(seconds=5))
        assert second_fence == 2
        lease = session.get(CheckLease, check.id)
        assert lease is not None
        assert lease.lease_fence == 2

        lease.lease_owner = "another-worker"
        lease.lease_expires_at = now + timedelta(seconds=90)
        lease.lease_fence = 7
        session.add(lease)
        session.commit()

        assert worker._acquire_lease(session, check, now + timedelta(seconds=10)) is None
        lease = session.get(CheckLease, check.id)
        assert lease is not None
        assert lease.lease_owner == "another-worker"
        assert lease.lease_fence == 7


def test_check_result_write_requires_current_fence_token(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_check_result_fence.sqlite'}"
    monkeypatch.setenv("WORKER_LEASES", "1")
    monkeypatch.setenv("WORKER_CLOCK_SKEW_TOLERANCE_SECONDS", "0")

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckLease, CheckResult
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_check_result_fence")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_check_result_fence",
            type=CheckType.HTTP,
            url="http://example.ok/health",
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        now = datetime.utcnow()
        lease = CheckLease(
            check_id=check.id,
            lease_owner="worker-primary",
            lease_expires_at=now + timedelta(seconds=120),
            lease_fence=9,
            updated_at=now,
        )
        session.add(lease)
        session.commit()

        worker._record_check_result(
            session,
            check,
            run_key="rk-1",
            status=CheckStatus.UP,
            latency_ms=12.3,
            error_message=None,
            incident_id=None,
            created_at=now,
            lease_owner="worker-primary",
            lease_fence=9,
        )
        rows = session.exec(select(CheckResult).where(CheckResult.check_id == check.id)).all()
        assert len(rows) == 1

        # Simulate lease takeover by another worker (new fence token).
        lease.lease_owner = "worker-secondary"
        lease.lease_fence = 10
        lease.updated_at = now + timedelta(seconds=1)
        session.add(lease)
        session.commit()

        worker._record_check_result(
            session,
            check,
            run_key="rk-2",
            status=CheckStatus.DOWN,
            latency_ms=None,
            error_message="timeout",
            incident_id=None,
            created_at=now + timedelta(seconds=1),
            lease_owner="worker-primary",
            lease_fence=9,
        )
        rows = session.exec(select(CheckResult).where(CheckResult.check_id == check.id)).all()
        assert len(rows) == 1


def test_flapping_suppression_skips_alert_send(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_flapping.sqlite'}"
    monkeypatch.setenv("FLAP_SUPPRESSION_ENABLED", "1")
    monkeypatch.setenv("FLAP_WINDOW_SECONDS", "3600")
    monkeypatch.setenv("FLAP_TRANSITIONS_THRESHOLD", "2")

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, AuditLog
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_flapping")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="hb_flap",
            type=CheckType.HEARTBEAT,
            expected_interval=60,
            grace_period=10,
            last_ping=datetime.utcnow() - timedelta(hours=2),
            status=CheckStatus.UP,
            alert_enabled=True,
            alert_after=1,
            alert_cooldown=0,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        down_calls = []

        def fake_notify_down(chk, proj, reason=None):
            if chk.id == check.id:
                down_calls.append(reason)

        monkeypatch.setattr(worker, "notify_down", fake_notify_down)
        monkeypatch.setattr(worker, "notify_recovery", lambda chk, proj: None)

        # 1) DOWN alert (not yet considered flapping)
        worker.scan_checks_once(session)
        # 2) recovery
        check.last_ping = datetime.utcnow()
        session.add(check)
        session.commit()
        worker.scan_checks_once(session)
        # 3) DOWN again -> flapping threshold reached, alert suppressed
        check.last_ping = datetime.utcnow() - timedelta(hours=2)
        session.add(check)
        session.commit()
        worker.scan_checks_once(session)

        assert len(down_calls) == 1
        suppression_logs = session.exec(
            select(AuditLog).where(
                AuditLog.action == "flapping_alert_suppressed",
                AuditLog.target_type == "check",
                AuditLog.target_id == check.id,
            )
        ).all()
        assert suppression_logs


def test_check_result_run_key_idempotency(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_check_result_run_key.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, CheckResult
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_run_key")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_idempotent",
            type=CheckType.HTTP,
            url="http://example.invalid/health",
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        now = datetime.utcnow()
        run_key = "poll:check:12345"
        worker._record_check_result(
            session,
            check,
            run_key=run_key,
            status=CheckStatus.DOWN,
            latency_ms=None,
            error_message="timeout",
            incident_id=None,
            created_at=now,
        )
        worker._record_check_result(
            session,
            check,
            run_key=run_key,
            status=CheckStatus.DOWN,
            latency_ms=None,
            error_message="timeout",
            incident_id=None,
            created_at=now + timedelta(seconds=1),
        )

        rows = session.exec(select(CheckResult).where(CheckResult.check_id == check.id)).all()
        assert len(rows) == 1
        assert rows[0].run_key == run_key


def test_event_run_key_idempotency(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_event_run_key.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_event_run_key")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_event_idempotent",
            type=CheckType.HTTP,
            url="http://example.invalid/health",
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        now = datetime.utcnow()
        run_key = "poll:event:12345"
        worker._record_event(
            session,
            check,
            run_key=run_key,
            event_type=EventType.DOWN,
            message="timeout",
            incident_id=None,
            created_at=now,
        )
        session.commit()

        worker._record_event(
            session,
            check,
            run_key=run_key,
            event_type=EventType.DOWN,
            message="timeout",
            incident_id=None,
            created_at=now + timedelta(seconds=1),
        )
        session.commit()

        rows = session.exec(select(Event).where(Event.check_id == check.id)).all()
        assert len(rows) == 1
        assert rows[0].run_key == run_key


def test_incident_open_run_key_idempotency(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incident_run_key.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Incident
    from src import worker

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_incident_run_key")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="http_incident_idempotent",
            type=CheckType.HTTP,
            url="http://example.invalid/health",
            status=CheckStatus.UP,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        now = datetime.utcnow()
        run_key = "poll:incident:12345"
        incident, created = worker._get_or_create_open_incident(
            session,
            check,
            signature="timeout",
            now=now,
            run_key=run_key,
        )
        assert incident is not None
        assert created is True

        # Resolve it, then re-run the same create path with identical run_key.
        worker._resolve_open_incident(session, incident, now=now + timedelta(seconds=1), run_key="resolve:123")
        incident_retry, created_retry = worker._get_or_create_open_incident(
            session,
            check,
            signature="timeout",
            now=now + timedelta(seconds=2),
            run_key=run_key,
        )
        assert incident_retry is not None
        assert incident_retry.id == incident.id
        assert created_retry is False

        rows = session.exec(select(Incident).where(Incident.check_id == check.id)).all()
        assert len(rows) == 1
        assert rows[0].open_run_key == run_key

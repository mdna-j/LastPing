import os
from datetime import datetime, timedelta

def test_send_email_monkeypatch(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            pass

        def starttls(self):
            pass

        def login(self, user, pw):
            pass

        def send_message(self, msg):
            sent['msg'] = msg

        def quit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr('smtplib.SMTP', lambda host, port: FakeSMTP(host, port))

    os.environ['SMTP_HOST'] = 'smtp.example'
    os.environ['SMTP_PORT'] = '587'
    os.environ['ALERT_EMAIL_FROM'] = 'noreply@example.com'
    os.environ['ALERT_EMAIL_TO'] = 'ops@example.com'

    from src.alerts import send_email

    ok = send_email('subj', 'body')
    assert ok
    assert 'msg' in sent


def test_project_throttle_escalation(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db5.sqlite'}"
    os.environ['ALERT_ESCALATION_EMAIL'] = 'esc@example.com'

    from sqlmodel import Session
    from src import db as dbmod
    from src.models import Project, Check, CheckType, CheckStatus, Event, EventType
    from src import worker
    from src import alerts

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name='throt', api_key='k', alert_rate_limit_count=1, alert_rate_limit_window=3600)
        session.add(project)
        session.commit()
        session.refresh(project)

        # create an existing DOWN event within window to trigger throttle
        ev = Event(check_id=0, project_id=project.id, event_type=EventType.DOWN, message='old')
        session.add(ev)
        session.commit()

        old = datetime.utcnow() - timedelta(hours=2)
        check = Check(project_id=project.id, name='hb_thr', type=CheckType.HEARTBEAT, expected_interval=60, grace_period=10, last_ping=old, status=CheckStatus.UP)
        session.add(check)
        session.commit()
        session.refresh(check)

        called = {}

        def fake_notify_escalation(proj, reason, check=None):
            called['esc'] = reason
            return True

        monkeypatch.setattr(alerts, 'notify_escalation', fake_notify_escalation)

        worker.scan_checks_once(session)

        assert 'esc' in called


def test_notify_escalation_uses_project_webhooks(monkeypatch):
    from src import alerts

    calls = []

    def fake_post_json(url, payload, timeout=10):
        calls.append((url, payload))
        return True

    monkeypatch.setattr(alerts, "_post_json", fake_post_json)

    class DummyProject:
        name = "projx"
        discord_webhook_url = "https://discord.test/hook"
        slack_webhook_url = None
        pagerduty_integration_key = None
        generic_webhook_url = None

    ok = alerts.notify_escalation(DummyProject(), "threshold exceeded")
    assert ok
    assert calls

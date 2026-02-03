from datetime import datetime

from lastping_sdk.client import HeartbeatClient
from lastping_sdk.decorators import heartbeat, heartbeat_context


def test_send_heartbeat_calls_requests_post(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass


    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json, timeout))
        return DummyResponse()


    monkeypatch.setattr("requests.post", fake_post)

    c = HeartbeatClient("http://example.com", "key")
    c.send(1, "check1", timestamp=datetime(2020, 1, 1))

    assert len(calls) == 1
    url, headers, json_body, timeout = calls[0]
    assert url == "http://example.com/projects/1/heartbeat/check1"
    assert headers["Authorization"] == "Bearer key"
    assert json_body["timestamp"] == "2020-01-01T00:00:00"


def test_decorator_sends_heartbeat(monkeypatch):
    sent = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass


    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(url)
        return DummyResponse()


    monkeypatch.setattr("requests.post", fake_post)

    @heartbeat(project_id=1, name="chk", base_url="http://example.com", api_key="k")
    def f(a, b):
        return a + b

    assert f(1, 2) == 3
    assert sent == ["http://example.com/projects/1/heartbeat/chk"]


def test_decorator_captures_errors(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)

    @heartbeat(project_id=1, name="chk", base_url="http://example.com", api_key="k", capture_errors=True)
    def fail():
        raise ValueError("boom")

    try:
        fail()
    except ValueError:
        pass

    assert len(calls) == 2
    assert calls[0][0].endswith("/projects/1/heartbeat/chk")
    assert calls[1][0].endswith("/projects/1/webhook")


def test_heartbeat_context_captures_errors(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)

    try:
        with heartbeat_context(1, "chk", "http://example.com", "k"):
            raise RuntimeError("fail")
    except RuntimeError:
        pass

    assert len(calls) == 2
    assert calls[0][0].endswith("/projects/1/heartbeat/chk")
    assert calls[1][0].endswith("/projects/1/webhook")


def test_client_context_captures_errors(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)

    client = HeartbeatClient("http://example.com", "k")
    try:
        with client.heartbeat_context(1, "chk", capture_errors=True):
            raise RuntimeError("fail")
    except RuntimeError:
        pass

    assert len(calls) == 2
    assert calls[0][0].endswith("/projects/1/heartbeat/chk")
    assert calls[1][0].endswith("/projects/1/webhook")


def test_client_run_with_heartbeat(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)

    client = HeartbeatClient("http://example.com", "k")
    result = client.run_with_heartbeat(1, "chk", lambda: 5)
    assert result == 5
    assert calls[0][0].endswith("/projects/1/heartbeat/chk")

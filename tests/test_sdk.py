from datetime import datetime

from lastping_sdk.client import HeartbeatClient
from lastping_sdk.decorators import heartbeat


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

import pytest

pytest.importorskip("aiohttp")

import asyncio

from lastping_sdk.async_client import AsyncHeartbeatClient, heartbeat_context


@pytest.mark.asyncio
async def test_async_client_send(monkeypatch):
    called = {}

    class DummyResp:
        def __init__(self):
            self._text = "ok"

        async def text(self):
            return self._text

        def raise_for_status(self):
            pass

    class DummySession:
        def __init__(self):
            pass

        async def post(self, url, headers=None, json=None, timeout=None):
            called['url'] = url
            return DummyResp()

        async def close(self):
            pass

    async def fake_ctor():
        return DummySession()

    # monkeypatch aiohttp.ClientSession to return a dummy session
    import aiohttp

    orig = aiohttp.ClientSession

    class FakeClientSession:
        def __init__(self):
            self._s = DummySession()

        async def post(self, url, headers=None, json=None, timeout=None):
            called['url'] = url
            return DummyResp()

        async def close(self):
            pass

    monkeypatch.setattr(aiohttp, 'ClientSession', FakeClientSession)

    async with AsyncHeartbeatClient("http://example.com", "k") as c:
        text = await c.send(1, "chk")
        assert 'http://example.com/projects/1/heartbeat/chk' in called['url']


@pytest.mark.asyncio
async def test_async_heartbeat_context_error_capture(monkeypatch):
    calls = []

    class DummyResp:
        def __init__(self):
            self._text = "ok"

        async def text(self):
            return self._text

        async def json(self):
            return {"ok": True}

        def raise_for_status(self):
            pass

    class FakeClientSession:
        async def post(self, url, headers=None, json=None, timeout=None):
            calls.append(url)
            return DummyResp()

        async def close(self):
            pass

    import aiohttp

    monkeypatch.setattr(aiohttp, 'ClientSession', FakeClientSession)

    try:
        async with heartbeat_context(1, "chk", "http://example.com", "k"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert any(u.endswith("/projects/1/heartbeat/chk") for u in calls)
    assert any(u.endswith("/projects/1/webhook") for u in calls)

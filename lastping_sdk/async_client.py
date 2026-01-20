from datetime import datetime
from typing import Optional, Any


class AsyncHeartbeatClient:
    """Async variant of HeartbeatClient using `aiohttp`.

    Usage:
        async with AsyncHeartbeatClient("https://example.com", "key") as c:
            await c.send(1, "check")
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # use a generic Any annotation to avoid importing aiohttp at module import time
        self._session: Optional[Any] = None

    async def __aenter__(self):
        try:
            import aiohttp
        except Exception as exc:  # pragma: no cover - environment may lack aiohttp
            raise ImportError("aiohttp is required for AsyncHeartbeatClient") from exc

        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.close()

    async def send(self, project_id: int, name: str, timestamp: Optional[datetime] = None):
        if self._session is None:
            try:
                import aiohttp
            except Exception as exc:  # pragma: no cover
                raise ImportError("aiohttp is required for AsyncHeartbeatClient") from exc
            self._session = aiohttp.ClientSession()

        url = f"{self.base_url}/projects/{project_id}/heartbeat/{name}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {}
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()

        # Await the post coroutine to get a response object (works with real aiohttp and with test fakes)
        resp = await self._session.post(url, headers=headers, json=payload or None, timeout=self.timeout)
        resp.raise_for_status()
        return await resp.text()
from datetime import datetime
from typing import Optional, Any


class AsyncHeartbeatClient:
    """Async variant of HeartbeatClient using `aiohttp`.

    Usage:
        async with AsyncHeartbeatClient("https://example.com", "key") as c:
            await c.send(1, "check")
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # use a generic Any annotation to avoid importing aiohttp at module import time
        self._session: Optional[Any] = None

    async def __aenter__(self):
        try:
            import aiohttp
        except Exception as exc:  # pragma: no cover - environment may lack aiohttp
            raise ImportError("aiohttp is required for AsyncHeartbeatClient") from exc

        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.close()

    async def send(self, project_id: int, name: str, timestamp: Optional[datetime] = None):
        if self._session is None:
            try:
                import aiohttp
            except Exception as exc:  # pragma: no cover
                raise ImportError("aiohttp is required for AsyncHeartbeatClient") from exc
            self._session = aiohttp.ClientSession()

        url = f"{self.base_url}/projects/{project_id}/heartbeat/{name}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {}
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()

        # Await the post coroutine to get a response object (works with real aiohttp and with test fakes)
        resp = await self._session.post(url, headers=headers, json=payload or None, timeout=self.timeout)
        resp.raise_for_status()
        return await resp.text()

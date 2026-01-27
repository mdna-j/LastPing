from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Any, Callable
from functools import wraps
import traceback


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

    async def _ensure_session(self):
        if self._session is None:
            try:
                import aiohttp
            except Exception as exc:  # pragma: no cover
                raise ImportError("aiohttp is required for AsyncHeartbeatClient") from exc
            self._session = aiohttp.ClientSession()

    async def send(self, project_id: int, name: str, timestamp: Optional[datetime] = None):
        await self._ensure_session()
        url = f"{self.base_url}/projects/{project_id}/heartbeat/{name}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {}
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()

        resp = await self._session.post(url, headers=headers, json=payload or None, timeout=self.timeout)
        resp.raise_for_status()
        return await resp.text()

    async def send_event(self, project_id: int, check_name: str, event: str = "down", message: Optional[str] = None, timestamp: Optional[datetime] = None):
        await self._ensure_session()
        url = f"{self.base_url}/projects/{project_id}/webhook"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"check_name": check_name, "event": event}
        if message is not None:
            payload["message"] = message
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()

        resp = await self._session.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        try:
            return await resp.json()
        except Exception:
            return await resp.text()


def _format_exception(exc: Exception, include_traceback: bool = False, max_len: int = 1200) -> str:
    if not include_traceback:
        return f"{exc.__class__.__name__}: {exc}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    tb = tb.strip()
    if len(tb) > max_len:
        tb = tb[-max_len:]
    return tb


def async_heartbeat(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = False, error_event: str = "down", include_traceback: bool = False) -> Callable[[Callable], Callable]:
    """Async decorator that sends a heartbeat and optionally reports exceptions."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with AsyncHeartbeatClient(base_url, api_key) as client:
                try:
                    await client.send(project_id, name)
                except Exception:
                    pass
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if capture_errors:
                        try:
                            msg = _format_exception(exc, include_traceback=include_traceback)
                            await client.send_event(project_id, name, event=error_event, message=f"exception: {msg}")
                        except Exception:
                            pass
                    raise
        return wrapper
    return decorator


@asynccontextmanager
async def heartbeat_context(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = True, error_event: str = "down", include_traceback: bool = False):
    """Async context manager that sends a heartbeat and reports exceptions."""
    async with AsyncHeartbeatClient(base_url, api_key) as client:
        try:
            await client.send(project_id, name)
        except Exception:
            pass
        try:
            yield
        except Exception as exc:
            if capture_errors:
                try:
                    msg = _format_exception(exc, include_traceback=include_traceback)
                    await client.send_event(project_id, name, event=error_event, message=f"exception: {msg}")
                except Exception:
                    pass
            raise

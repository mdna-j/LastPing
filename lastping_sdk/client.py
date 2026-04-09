import requests
from contextlib import contextmanager
from datetime import datetime
import traceback
from typing import Optional, Any, Dict, Callable, Iterator, TypeVar

T = TypeVar("T")


def _format_exception(exc: Exception, include_traceback: bool = False, max_len: int = 1200) -> str:
    if not include_traceback:
        return f"{exc.__class__.__name__}: {exc}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb) > max_len:
        tb = tb[:max_len] + "...(truncated)"
    return tb


class HeartbeatClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def send(self, project_id: int, name: str, timestamp: Optional[datetime] = None) -> requests.Response:
        url = f"{self.base_url}/projects/{project_id}/heartbeat/{name}"
        headers = {"X-API-KEY": self.api_key}
        payload = {}

        if timestamp is not None:
            if not isinstance(timestamp, datetime):
                raise TypeError("timestamp must be a datetime.datetime")
            payload["timestamp"] = timestamp.isoformat()

        resp = requests.post(url, headers=headers, json=payload or None, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def send_event(self, project_id: int, check_name: str, event: str = "down", message: Optional[str] = None, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/projects/{project_id}/webhook"
        headers = {"X-API-KEY": self.api_key}
        payload: Dict[str, Any] = {"check_name": check_name, "event": event}
        if message is not None:
            payload["message"] = message
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()

        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code}

    def run_with_heartbeat(
        self,
        project_id: int,
        name: str,
        func: Callable[[], T],
        capture_errors: bool = False,
        error_event: str = "down",
        include_traceback: bool = False,
    ) -> T:
        """Send a heartbeat, run a function, and optionally report exceptions."""
        self.send(project_id, name)
        try:
            return func()
        except Exception as exc:
            if capture_errors:
                try:
                    msg = _format_exception(exc, include_traceback=include_traceback)
                    self.send_event(project_id, name, event=error_event, message=f"exception: {msg}")
                except Exception:
                    pass
            raise

    @contextmanager
    def heartbeat_context(
        self,
        project_id: int,
        name: str,
        capture_errors: bool = True,
        error_event: str = "down",
        include_traceback: bool = False,
    ) -> Iterator[None]:
        """Context manager that sends a heartbeat and optionally reports exceptions."""
        try:
            self.send(project_id, name)
        except Exception:
            pass
        try:
            yield
        except Exception as exc:
            if capture_errors:
                try:
                    msg = _format_exception(exc, include_traceback=include_traceback)
                    self.send_event(project_id, name, event=error_event, message=f"exception: {msg}")
                except Exception:
                    pass
            raise

from contextlib import contextmanager
from functools import wraps
from typing import Callable, Optional
import traceback

from .client import HeartbeatClient
from .api import send_event


def _format_exception(exc: Exception, include_traceback: bool = False, max_len: int = 1200) -> str:
    if not include_traceback:
        return f"{exc.__class__.__name__}: {exc}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    tb = tb.strip()
    if len(tb) > max_len:
        tb = tb[-max_len:]
    return tb


def heartbeat(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = False, error_event: str = "down", include_traceback: bool = False) -> Callable[[Callable], Callable]:
    """Decorator that sends a heartbeat before calling the wrapped function.

    Usage:
        @heartbeat(1, "my-check", "https://example.com", "key")
        def work():
            ...
    """
    client = HeartbeatClient(base_url, api_key)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                client.send(project_id, name)
            except Exception:
                # best-effort: swallow errors so decorator doesn't break caller
                pass
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if capture_errors:
                    try:
                        msg = _format_exception(exc, include_traceback=include_traceback)
                        send_event(base_url, api_key, project_id, name, event=error_event, message=f"exception: {msg}")
                    except Exception:
                        pass
                raise

        return wrapper

    return decorator


@contextmanager
def heartbeat_context(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = True, error_event: str = "down", include_traceback: bool = False):
    """Context manager that sends a heartbeat and optionally reports exceptions."""
    client = HeartbeatClient(base_url, api_key)
    try:
        client.send(project_id, name)
    except Exception:
        pass
    try:
        yield
    except Exception as exc:
        if capture_errors:
            try:
                msg = _format_exception(exc, include_traceback=include_traceback)
                send_event(base_url, api_key, project_id, name, event=error_event, message=f"exception: {msg}")
            except Exception:
                pass
        raise

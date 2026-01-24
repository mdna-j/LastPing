from contextlib import contextmanager
from functools import wraps
from typing import Callable, Optional

from .client import HeartbeatClient
from .api import send_event


def heartbeat(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = False, error_event: str = "down") -> Callable[[Callable], Callable]:
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
                        send_event(base_url, api_key, project_id, name, event=error_event, message=f"exception: {exc}")
                    except Exception:
                        pass
                raise

        return wrapper

    return decorator


@contextmanager
def heartbeat_context(project_id: int, name: str, base_url: str, api_key: str, capture_errors: bool = True, error_event: str = "down"):
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
                send_event(base_url, api_key, project_id, name, event=error_event, message=f"exception: {exc}")
            except Exception:
                pass
        raise

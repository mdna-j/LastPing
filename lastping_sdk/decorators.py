from functools import wraps
from typing import Callable

from .client import HeartbeatClient


def heartbeat(project_id: int, name: str, base_url: str, api_key: str):
    """Decorator that sends a heartbeat before calling the wrapped function.

    Usage:
        @heartbeat(1, "my-check", "https://example.com", "key")
        def work():
            ...
    """
    client = HeartbeatClient(base_url, api_key)

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                client.send(project_id, name)
            except Exception:
                # best-effort: swallow errors so decorator doesn't break caller
                pass
            return func(*args, **kwargs)

        return wrapper

    return decorator

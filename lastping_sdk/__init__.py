"""LastPing Python SDK (minimal).

Provides a tiny client for sending heartbeats and helper utilities.
"""
try:
    from importlib.metadata import PackageNotFoundError, version
except Exception:  # pragma: no cover
    PackageNotFoundError = Exception  # type: ignore
    version = None  # type: ignore

try:
    __version__ = version("lastping-sdk") if version is not None else "0.0.0"
except PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0"

from .client import HeartbeatClient
from .async_client import AsyncHeartbeatClient, heartbeat_context as async_heartbeat_context
from .decorators import heartbeat, heartbeat_context
from .api import send_event

__all__ = [
    "HeartbeatClient",
    "AsyncHeartbeatClient",
    "heartbeat",
    "heartbeat_context",
    "async_heartbeat_context",
    "send_event",
    "__version__",
]

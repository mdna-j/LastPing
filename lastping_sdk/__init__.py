"""LastPing Python SDK (minimal)

Provides a tiny client for sending heartbeats and a decorator helper.
"""
__version__ = "0.1.0"

from .client import HeartbeatClient
from .async_client import AsyncHeartbeatClient
from .decorators import heartbeat
from .api import send_event

__all__ = ["HeartbeatClient", "AsyncHeartbeatClient", "heartbeat", "send_event", "__version__"]

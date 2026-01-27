from datetime import datetime
from typing import Any, Dict, Optional

from .client import HeartbeatClient


def send_event(base_url: str, api_key: str, project_id: int, check_name: str, event: str = "down", message: Optional[str] = None, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
    """Send a generic webhook-style event to LastPing using the projects webhook endpoint.

    This is a small helper for SDK consumers who want to create events.
    """
    hb = HeartbeatClient(base_url, api_key)
    payload = {"check_name": check_name, "event": event}
    if message is not None:
        payload["message"] = message
    if timestamp is not None:
        payload["timestamp"] = timestamp.isoformat()

    url = f"{hb.base_url}/projects/{project_id}/webhook"
    import requests

    resp = requests.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=hb.timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code}


def get_checks(base_url: str, api_key: str, project_id: int):
    """Return list of checks for a project (simple helper)."""
    import requests

    url = f"{base_url.rstrip('/')}/projects/{project_id}/checks/"
    resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=5)
    resp.raise_for_status()
    return resp.json()


def create_incident(base_url: str, api_key: str, project_id: int, title: str, body: Optional[str] = None):
    """Create an incident for a project (wrapper around incidents API)."""
    import requests

    url = f"{base_url.rstrip('/')}/projects/{project_id}/incidents"
    payload = {"title": title}
    if body:
        payload["body"] = body
    resp = requests.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()

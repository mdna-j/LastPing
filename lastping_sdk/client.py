import requests
from datetime import datetime
from typing import Optional, Any, Dict


class HeartbeatClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def send(self, project_id: int, name: str, timestamp: Optional[datetime] = None) -> requests.Response:
        url = f"{self.base_url}/projects/{project_id}/heartbeat/{name}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
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
        headers = {"Authorization": f"Bearer {self.api_key}"}
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

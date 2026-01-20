import requests
from datetime import datetime
from typing import Optional


class HeartbeatClient:
    """Minimal client to send heartbeats to a LastPing server.

    Example:
        c = HeartbeatClient("https://example.com", "my_api_key")
        c.send(project_id=1, name="check-name")
    """

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
import requests
from datetime import datetime
from typing import Optional


class HeartbeatClient:
    """Minimal client to send heartbeats to a LastPing server.

    Example:
        c = HeartbeatClient("https://example.com", "my_api_key")
        c.send(project_id=1, name="check-name")
    """

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
        import requests
        from datetime import datetime
        from typing import Optional


        class HeartbeatClient:
            """Minimal client to send heartbeats to a LastPing server.

            Example:
                c = HeartbeatClient("https://example.com", "my_api_key")
                c.send(project_id=1, name="check-name")
            """

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

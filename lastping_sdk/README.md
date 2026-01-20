# LastPing Python SDK (minimal)

Small helper for sending heartbeats to a LastPing server.

Quick example

```python
from lastping_sdk import HeartbeatClient

c = HeartbeatClient("https://example.com", "API_KEY")
c.send(project_id=1, name="my-check")
```

Decorator example

```python
from lastping_sdk import heartbeat

@heartbeat(1, "my-check", "https://example.com", "API_KEY")
def job():
    pass
```
# LastPing Python SDK (minimal)

Small helper for sending heartbeats to a LastPing server.

Quick example
```
from lastping_sdk import HeartbeatClient

c = HeartbeatClient("https://example.com", "API_KEY")
c.send(project_id=1, name="my-check")
```

Decorator example
```
from lastping_sdk import heartbeat

@heartbeat(1, "my-check", "https://example.com", "API_KEY")
def job():
    pass
```

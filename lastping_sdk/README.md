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

Context manager example

```python
from lastping_sdk import heartbeat_context

with heartbeat_context(1, "my-check", "https://example.com", "API_KEY"):
    run_job()
```

Error capture (with optional traceback)

```python
from lastping_sdk import heartbeat

@heartbeat(1, "my-check", "https://example.com", "API_KEY", capture_errors=True, include_traceback=True)
def job():
    ...
```

Async decorator example

```python
from lastping_sdk import async_heartbeat

@async_heartbeat(1, "my-check", "https://example.com", "API_KEY", capture_errors=True)
async def job():
    ...
```

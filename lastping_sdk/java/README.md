# LastPing Java SDK (stub)

This is a minimal stub to show how a Java client could call the LastPing API.
It is not packaged or published yet.

## Example

```
LastPingClient client = new LastPingClient("https://example.com", "API_KEY");
client.sendHeartbeat(1, "my-check");
```

## Send an event

```
client.sendEvent(1, "my-check", "down", "exception: timeout");
```

## Wrap a job with error capture

```
client.runWithHeartbeat(1, "my-check", () -> {
    // do work
}, true, "down");
```

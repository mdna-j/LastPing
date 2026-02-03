# LastPing Java SDK

This is a minimal Java client for the LastPing API.
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

## Send with timestamp

```
client.sendHeartbeat(1, "my-check", "2026-02-03T12:00:00Z");
client.sendEvent(1, "my-check", "down", "timeout", "2026-02-03T12:01:00Z");
```

## Wrap a job with error capture

```
client.runWithHeartbeat(1, "my-check", () -> {
    // do work
}, true, "down");
```

## Try-with-resources context

```
try (LastPingClient.HeartbeatContext ctx = client.heartbeatContext(1, "my-check")) {
    // do work
}
```

## Build (Gradle)

```
./gradlew build
```

Publish locally:

```
./gradlew publishToMavenLocal
```

## Build (Maven)

```
mvn -q -f pom.xml package
```

## Publish to GitHub Packages (Maven)

```
mvn -q -f pom.xml deploy -DskipTests \
  -DaltDeploymentRepository=github::https://maven.pkg.github.com/<OWNER>/<REPO>
```

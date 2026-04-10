# Chaos And Load Testing

This runbook covers repeatable failure-injection drills for the local Docker Compose stack and CI.

## Included drill script

- `scripts/chaos_load_drill.py`

The drill produces proof artifacts in:

- `artifacts/chaos_drill/summary.json`
- `artifacts/chaos_drill/summary.md`

## Scenarios covered

1. `api_load_burst`
   Runs a concurrent public-status request burst and records p50/p95/max latency.

2. `db_slowness`
   Uses `docker compose exec db psql ... LOCK TABLE "check"` plus `pg_sleep(...)` to block the dashboard-health query path and verify that latency actually spikes during contention.

3. `worker_failure`
   Stops the worker container, waits for the dashboard health surface to report overdue scheduled checks / worker lag, then restarts the worker and verifies recovery.

4. `redis_loss`
   Stops Redis and floods the public-status endpoint until rate limiting trips. This verifies the fallback limiter still holds when Redis is unavailable.

5. `alert_storm`
   Creates multiple intentionally failing HTTP checks so the worker opens incidents and fans out notifications under pressure.

6. `integration_outage`
   Routes alerts to an intentionally dead generic webhook target and verifies `notification_failed` evidence is recorded for retry visibility.

## Local usage

Start the stack:

```bash
docker compose up -d --build api worker db redis
```

Then run:

```bash
python scripts/chaos_load_drill.py \
  --base-url http://127.0.0.1:8000 \
  --admin-token "${ADMIN_TOKEN}" \
  --output-dir artifacts/chaos_drill \
  --format github
```

Recommended local preconditions:

- `.env` contains `DATABASE_URL`, `REDIS_URL`, `ADMIN_TOKEN`, and `LASTPING_ENCRYPTION_KEY`
- Docker Desktop is running
- the API is healthy at `http://127.0.0.1:8000/health`

## Scheduled CI drill

This repo includes `.github/workflows/chaos_drill.yml`.

The workflow:

- renders a local Compose `.env`
- starts `api`, `worker`, `db`, and `redis`
- waits for `/health`
- runs `scripts/chaos_load_drill.py`
- uploads `summary.json` / `summary.md`
- tears the stack down

## Evidence to review

For each run, check:

- scenario status (`ok` / `failed`)
- p95 and max latency from the load burst
- observed latency during DB lock contention
- worker-lag state after worker stop
- `429` counts during Redis loss
- number of incident / webhook failure rows generated during the alert storm

Treat failures here as production-readiness bugs, not just flaky test noise.

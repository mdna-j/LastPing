# Multi-Region Validation Plan (Production-like)

Use this checklist to validate multi-region behavior before production.
It assumes you run **one worker per region** with leases enabled and
failover enabled.

## Preconditions
- Distinct worker fleets per region (ex: `us-east`, `eu-west`, `ap-south`)
- Unique `WORKER_ID` per worker instance
- `WORKER_LEASE_SECONDS` set to 60–120s
- `WORKER_REGION_FAILOVER=1`
- `WORKER_FAILOVER_AFTER_SECONDS` set to 300s

## Acceptance criteria
- Only the matching region processes a region-pinned check.
- No cross-region processing until a lease exists and has expired + grace.
- Failover happens within `WORKER_LEASE_SECONDS + WORKER_FAILOVER_AFTER_SECONDS`.
- No duplicate processing while a valid lease exists.
- Recovery returns processing to the primary region after restart.

## Test cases

### 1) Primary region processing
1. Create a check with `region=us-east` and ensure it fails.
2. Confirm events are produced by the `us-east` worker.
3. Confirm the lease owner matches `us-east`.

### 2) Failover after lease expiry + grace
1. Stop the `us-east` worker.
2. Wait for lease expiry + grace.
3. Confirm another region acquires the lease and continues processing.

### 3) No early failover
1. Create a **new** region-pinned check with `region=us-east`.
2. Ensure non‑matching regions do **not** process it before the first lease.

### 4) Active-active region list
1. Set `region=us-east,eu-west` for a check.
2. Verify both regions can process over time, but leases prevent double-processing.

### 5) Recovery back to primary
1. Restart the `us-east` worker.
2. Confirm leases return to the primary region.

## Automation helpers

Local validation (docker-compose):
```
docker compose --profile multi-region up -d --build --scale worker=0
python scripts/validate_multi_region.py --region us-east --simulate-failover --stop-primary --start-primary
```

Lease and event audit report:
```
py -3.11 scripts/multi_region_report.py --recent-hours 2 --down-threshold 3
```

## Observability checklist
- Log entries show which worker is processing checks.
- `check_lease` table updated regularly (no stale `updated_at`).
- No burst of duplicate DOWN events for the same check in a short window.

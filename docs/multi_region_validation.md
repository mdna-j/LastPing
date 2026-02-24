# Multi-Region Validation Plan (Production-like)

Use this checklist to validate multi-region behavior before production.
It assumes you run one worker per region with leases and failover enabled.

## Preconditions
- Distinct worker fleets per region (for example: `us-east`, `eu-west`, `ap-south`)
- Unique `WORKER_ID` per worker instance
- `WORKER_LEASE_SECONDS` set to 60-120s
- `WORKER_REGION_FAILOVER=1`
- `WORKER_FAILOVER_AFTER_SECONDS` set to 300s

## Acceptance Criteria
- Only the matching region processes a region-pinned check.
- No cross-region processing until a lease exists and has expired plus grace.
- Failover happens within `WORKER_LEASE_SECONDS + WORKER_FAILOVER_AFTER_SECONDS`.
- No duplicate processing while a valid lease exists.
- Recovery returns processing to the primary region after restart.

## Test Cases

### 1) Primary region processing
1. Create a check with `region=us-east` and ensure it fails.
2. Confirm events are produced by the `us-east` worker.
3. Confirm the lease owner matches `us-east`.

### 2) Failover after lease expiry + grace
1. Stop the `us-east` worker.
2. Wait for lease expiry plus grace.
3. Confirm another region acquires the lease and continues processing.

### 3) No early failover
1. Create a new region-pinned check with `region=us-east`.
2. Ensure non-matching regions do not process it before the first lease.

### 4) Active-active region list
1. Set `region=us-east,eu-west` for a check.
2. Verify both regions can process over time while leases prevent double-processing.

### 5) Recovery back to primary
1. Restart the `us-east` worker.
2. Confirm leases return to the primary region.

## Automation Helpers

Local validation (docker-compose):
```bash
docker compose --profile multi-region up -d --build --scale worker=0
python scripts/validate_multi_region.py --region us-east --simulate-failover --stop-primary --start-primary
```

Lease and event audit report:
```bash
py -3.11 scripts/multi_region_report.py --recent-hours 2 --down-threshold 3
```

GitHub report workflow (staging DB):
```bash
# Workflow: .github/workflows/multi_region_report.yml
# Outputs:
# - artifacts/multi_region_report.json
# - artifacts/multi_region_signoff.md
```

## Staging Signoff Tracking (Required)
1. Run `Multi-Region Report` in GitHub Actions (or wait for schedule).
2. Download artifact `multi-region-signoff-<run_id>`.
3. Commit `multi_region_signoff.md` into `docs/signoffs/multi_region/` using file name:
   `YYYY-MM-DD_run-<run_id>_multi_region_signoff.md`.
4. Append one row in `docs/signoffs/multi_region/SIGNOFF_LOG.md` with:
   date, environment, workflow run URL, overall status, and reviewer.
5. Include the signoff commit link in your release/staging ticket.

## Observability Checklist
- Log entries show which worker is processing checks.
- `check_lease` table updates regularly (no stale `updated_at`).
- No burst of duplicate DOWN events for the same check in a short window.

# Multi-region workers + leases/failover

This document describes how LastPing handles multi-region workers, check leases, and failover.

## Region targeting

Each check can optionally include a `region` field:

- `null`/empty: any worker can process the check.
- Single region: `us-east` (only workers in that region).
- Multiple regions: `us-east, eu-west` (comma/space separated list).
- Wildcard: `*` or `all` (any worker region).

Workers advertise their region via the `WORKER_REGION` env var. The worker will only process checks that match its region list unless failover is enabled.

## Leases and concurrency control

Workers acquire a lease per check in the `check_lease` table. Only one worker at a time holds the lease:

- `WORKER_LEASES=0` disables leases (all workers can process all checks).
- `WORKER_LEASE_SECONDS` controls lease duration (default: 120s).
- Lease owner is `WORKER_ID` or `WORKER_REGION` or hostname.

## Failover behavior

If a region becomes unhealthy, workers in other regions can take over after lease expiry:

- `WORKER_REGION_FAILOVER=1` enables cross-region failover.
- `WORKER_FAILOVER_AFTER_SECONDS` defines how long after lease expiry another region can take over (default: 300s).

When failover is enabled, workers will attempt to acquire leases for checks outside their region **only** after the grace period has passed **and** a lease exists.
This prevents non-matching regions from immediately claiming new checks before the owning region has ever processed them.

## Suggested deployment pattern

- Run one worker per region with `WORKER_REGION` set.
- Enable leases (default) and keep `WORKER_LEASE_SECONDS` short (60-120s).
- Enable failover for redundancy.
- For critical checks, set `region` to a list of regions for active-active checking.

## Example env

```
WORKER_REGION=us-east
WORKER_LEASE_SECONDS=90
WORKER_REGION_FAILOVER=1
WORKER_FAILOVER_AFTER_SECONDS=300
```

## Docker Compose profile (local simulation)

The compose file includes a `multi-region` profile with three workers:
`worker_us`, `worker_eu`, and `worker_ap`. Each sets `WORKER_REGION` and
enables `WORKER_REGION_FAILOVER=1`.

Suggested command:

```
docker compose --profile multi-region up -d --build
```

If you want to avoid running the default single worker, use:

```
docker compose --profile multi-region up -d --build --scale worker=0
```

## Health + failover simulation checklist

1) Start stack with multi-region workers:

```
docker compose --profile multi-region up -d --build --scale worker=0
```

2) Confirm workers are healthy:

```
docker compose ps
```

3) Create a check pinned to a region (e.g. `region=us-east`) and make it fail
   (or create a heartbeat check and stop sending pings).

4) Watch the primary region worker log the failure:

```
docker compose logs -f worker_us
```

5) Simulate region outage:

```
docker compose stop worker_us
```

6) Wait for lease expiry + failover grace:
   - `WORKER_LEASE_SECONDS` (default 120s)
   - `WORKER_FAILOVER_AFTER_SECONDS` (default 300s)

7) Verify another region picked it up:

```
docker compose logs -f worker_eu
```

8) Recover the primary region:

```
docker compose start worker_us
```

9) Optional: set `region=us-east,eu-west` on a check to observe active-active
   checks while leases prevent double-processing.

## Automated validation (optional)

For a repeatable local check, use the helper script which creates a region-pinned
heartbeat check, waits for a lease, and can optionally stop the primary worker
to observe failover:

```
python scripts/validate_multi_region.py --region us-east --simulate-failover --stop-primary --start-primary
```

Notes:
- The script expects `docker compose` to be available if you pass `--stop-primary`.
- For faster tests, lower `WORKER_LEASE_SECONDS` and `WORKER_FAILOVER_AFTER_SECONDS`.

## Production-like validation

See `docs/multi_region_validation.md` for a staging checklist and acceptance criteria,
plus the `scripts/multi_region_report.py` helper.

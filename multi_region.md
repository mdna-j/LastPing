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

When failover is enabled, workers will attempt to acquire leases for checks outside their region **only** after the grace period has passed.

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

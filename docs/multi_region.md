# Multi-Region Worker Design (Draft)

## Goals
- Allow multiple worker fleets to run in separate regions.
- Route checks to a specific region or run them in all regions.
- Provide a basic failover path when a region is down.

## Approach
1. Add a `region` field to checks.
2. Each worker runs with `WORKER_REGION=<region>` and a unique `WORKER_ID`.
3. Workers process:
   - Checks with `region` matching `WORKER_REGION`
   - Checks with no region (global)
4. A lease table (`check_lease`) ensures only one worker processes a check at a time.
   - Workers acquire/renew leases for a short TTL (default 120s).
   - If a worker goes down, leases expire and another worker can take over.

## Failover Strategy
- Keep checks unassigned (`region` null) for active-active execution.
- For assigned checks, a secondary worker can be started with the same `WORKER_REGION`.
- Lease expiry provides automatic reassignment to another worker when the current one stops renewing.
- Use `WORKER_LEASE_SECONDS` to tune failover sensitivity.

## Future Enhancements
- Region priority list per check.
- Lease/lock mechanism for stronger single-region execution.
- Health-based region reassignment.

## Local validation
See `multi_region.md` at repo root and `scripts/validate_multi_region.py` for
the current multi-region simulation checklist and helper script.

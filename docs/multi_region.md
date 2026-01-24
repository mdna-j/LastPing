# Multi-Region Worker Design (Draft)

## Goals
- Allow multiple worker fleets to run in separate regions.
- Route checks to a specific region or run them in all regions.
- Provide a basic failover path when a region is down.

## Approach
1. Add a `region` field to checks.
2. Each worker runs with `WORKER_REGION=<region>`.
3. Workers process:
   - Checks with `region` matching `WORKER_REGION`
   - Checks with no region (global)

## Failover Strategy
- Keep checks unassigned (`region` null) for active-active execution.
- For assigned checks, a secondary worker can be started with the same region value.
- Use a shared DB and `next_run` timestamps to avoid duplicate polling.

## Future Enhancements
- Region priority list per check.
- Lease/lock mechanism for stronger single-region execution.
- Health-based region reassignment.

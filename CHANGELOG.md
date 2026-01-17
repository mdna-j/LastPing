## Unreleased

- Persist scheduling for HTTP checks: add `interval` and `next_run` to `Check` and have worker persist `next_run` after running checks. See:
  - `src/models.py` (new fields)
  - `src/worker.py` (honor and persist `next_run`)
  - `alembic/versions/0002_add_interval_next_run.py` (migration)
  - Tests updated: `tests/test_worker.py`

- Incident grouping, merge/split and improved audit context:
  - Add `Incident.group_id` and `Incident.merged_into` to support grouping of related incidents.
  - Worker groups related failures within a time window to reuse incidents.
  - Add `merge` and `split` endpoints for incidents with RBAC and audit logging.
  - Extend `AuditLog` with `actor_ip` and `user_agent` and backfill where appropriate.
  - Alembic revisions: `alembic/versions/0017_add_incident_grouping.py`, `0018_add_audit_fields_and_backfill_group_id.py`.
  - Tests: add incident grouping/merge/split tests; full test suite passes locally.
  - Scripts: `scripts/e2e_smoke.py` added to run a fast merge/split smoke flow against the dev DB.

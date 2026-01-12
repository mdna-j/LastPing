## Unreleased

- Persist scheduling for HTTP checks: add `interval` and `next_run` to `Check` and have worker persist `next_run` after running checks. See:
  - `src/models.py` (new fields)
  - `src/worker.py` (honor and persist `next_run`)
  - `alembic/versions/0002_add_interval_next_run.py` (migration)
  - Tests updated: `tests/test_worker.py`

Migration checklist for revisions 0007-0009

1. Backup your production database

- Always take a full backup or snapshot before applying schema changes.

2. Review Alembic revisions

- Revisions to apply: `0007_add_api_key_model.py`, `0008_add_api_key_usage.py`, `0009_add_audit_log.py`.
- Inspect files in `alembic/versions/` and ensure they match your target DB dialect (SQLite dev vs Postgres production).

3. Test locally against a DB copy

- Create a copy of production DB and run migrations:

```bash
export DATABASE_URL=postgresql://user:pw@host:5432/dbname
alembic upgrade head
```

- For SQLite, migrations include recreate-table logic; validate data integrity after upgrade.

4. Staging run

- Deploy to staging identical to production environment and run the migrations there first.

5. Apply to production during a maintenance window

- Notify stakeholders and ensure you can rollback (DB restore) if needed.
- Run:

```bash
alembic upgrade head
```

6. Post-migration checks

- Verify tables exist: `api_key`, `api_key_usage`, `audit_log`.
- Run smoke tests and sample API calls (list projects, create API key via admin, ensure audit logs recorded).

7. Rollback plan

- If anything fails, restore DB from backup/snapshot and roll back code changes.

Notes

- If you're running multiple web/worker instances, stop workers while migrating to avoid race conditions with newly added tables until migrations complete.
- For distributed rate limiting, set `REDIS_URL` and ensure Redis is available to workers after migration.

# LastPing Docs

This `docs/` folder contains brief developer-facing documentation and notes.

Contents:

- `README.md` - this file (docs index)
- `analytics_ml_ops.md` - predictive model training and drift monitoring runbook
- `backup_restore.md` - PostgreSQL backup / restore runbook for staging and production
- `chaos_testing.md` - repeatable load and chaos drills for worker failure, DB contention, Redis loss, alert storms, and integration outages
- `deployment.md` - staging / production deployment flow, GitHub Environment setup, and Helm usage
- `raw_retention_ops.md` - large-table retention tuning (chunked deletes, archival, partitioning guidance)

Developer notes:

- See the project `README.md` for quickstart and environment variables.
- CI: `.github/workflows/ci.yml` runs linting and tests.
- Deployments: `.github/workflows/deploy.yml` validates runtime env and rolls out the Helm chart in `deploy/helm/lastping`.
- Restore drills: `.github/workflows/backup_restore_drill.yml` verifies that backups can be restored and migrated successfully.
- Chaos drills: `.github/workflows/chaos_drill.yml` exercises worker failure, DB slowness, Redis loss, alert storms, and integration outage capture.
- Consider adding Sphinx or MkDocs if you want user-facing documentation pages.

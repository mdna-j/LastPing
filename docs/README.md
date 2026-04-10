# LastPing Docs

This `docs/` folder contains brief developer-facing documentation and notes.

Contents:

- `README.md` - this file (docs index)
- `analytics_ml_ops.md` - predictive model training and drift monitoring runbook
- `backup_restore.md` - PostgreSQL backup / restore runbook for staging and production
- `deployment.md` - staging / production deployment flow, GitHub Environment setup, and Helm usage
- `raw_retention_ops.md` - large-table retention tuning (chunked deletes, archival, partitioning guidance)

Developer notes:

- See the project `README.md` for quickstart and environment variables.
- CI: `.github/workflows/ci.yml` runs linting and tests.
- Deployments: `.github/workflows/deploy.yml` validates runtime env and rolls out the Helm chart in `deploy/helm/lastping`.
- Restore drills: `.github/workflows/backup_restore_drill.yml` verifies that backups can be restored and migrated successfully.
- Consider adding Sphinx or MkDocs if you want user-facing documentation pages.

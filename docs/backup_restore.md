# Backup And Restore

This runbook covers database backup / restore for staging and production deployments.

## What to protect

At minimum, back up:

- PostgreSQL data
- Kubernetes secret manifests for deployment config
- GitHub Environment secret ownership / rotation documentation

Redis is treated as ephemeral cache / rate-limit state and is not the source of truth.

## Backup strategy

Use both of these:

1. Managed database snapshots / volume snapshots
2. Logical PostgreSQL dumps

### Logical backup example

```bash
pg_dump "$DATABASE_URL" --format=custom --file "lastping-$(date +%F-%H%M).dump"
```

Recommended cadence:

- staging: daily
- production: at least daily, plus before schema changes

## Pre-upgrade backup checklist

Before production deploys that include migrations:

1. Confirm the target image tag and Helm revision.
2. Take a PostgreSQL backup.
3. Record current migration head:

```bash
python -m alembic current
```

4. Record current release:

```bash
helm history lastping -n lastping-production
```

## Restore flow

### 1. Stop writes

Scale the API and workers down, or redirect traffic away from the environment.

Examples:

```bash
kubectl scale deployment lastping-api --replicas=0 -n lastping-production
kubectl scale deployment lastping-worker-us-east --replicas=0 -n lastping-production
kubectl scale deployment lastping-worker-eu-west --replicas=0 -n lastping-production
kubectl scale deployment lastping-worker-ap-south --replicas=0 -n lastping-production
```

### 2. Restore PostgreSQL

```bash
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" lastping-restore.dump
```

If you restore into a brand-new database, run:

```bash
python -m alembic upgrade head
```

### 3. Restore runtime config

Re-apply the deployment env secret:

```bash
kubectl create secret generic lastping-env \
  --namespace lastping-production \
  --from-env-file deploy.runtime.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4. Bring services back

```bash
helm upgrade --install lastping deploy/helm/lastping \
  -n lastping-production \
  -f deploy/helm/lastping/values.yaml \
  -f deploy/helm/lastping/values-production.yaml
```

### 5. Validate recovery

- `curl -fsS ${BASE_URL}/health`
- load the dashboard
- load the public status page
- confirm workers are healthy
- confirm recent incidents / postmortems / subscriptions still exist

## Staging restore drill

Run a restore drill in staging on a schedule:

1. restore a recent backup
2. run `python -m alembic upgrade head`
3. run `python -m pytest -q` against a throwaway database when practical
4. verify `/health` and `/ui/status/{project_id}`

## Automated backup verification and restore drill

This repo now includes a scheduled GitHub Actions workflow:

- `.github/workflows/backup_restore_drill.yml`

Required GitHub Environment / repository secret:

- `BACKUP_DRILL_SOURCE_DATABASE_URL`

What the workflow does:

1. starts an isolated PostgreSQL service on the runner
2. takes a logical backup from `BACKUP_DRILL_SOURCE_DATABASE_URL`
3. restores that dump into the isolated PostgreSQL target
4. runs `python -m alembic upgrade head` against the restored database
5. compares high-signal table counts before and after restore
6. uploads proof artifacts without uploading the live database dump itself

Artifact output:

- `artifacts/backup_restore_drill/summary.json`
- `artifacts/backup_restore_drill/summary.md`

The summary records:

- redacted source / restore database URLs
- backup sha256 and size
- backup / restore / migration durations
- verification table counts before / after restore
- any mismatched counts
- detected Alembic head after restore

### Local restore drill

For SQLite:

```bash
python scripts/backup_restore_drill.py \
  --source-url "sqlite:///./dev.db" \
  --restore-url "sqlite:///./restore-drill.db" \
  --output-dir artifacts/backup_restore_drill
```

For PostgreSQL:

```bash
python scripts/backup_restore_drill.py \
  --source-url "$DATABASE_URL" \
  --restore-url "postgresql://postgres:postgres@localhost:5432/lastping_restore" \
  --output-dir artifacts/backup_restore_drill
```

Use `--keep-backup` only for controlled local debugging. The scheduled workflow deletes the dump after hashing it and only keeps summary evidence.

## Secrets and compliance notes

- Never commit real `deploy.runtime.env` files.
- Rotate `ADMIN_TOKEN` and deployment secrets after any recovery event involving operator access.
- Store backup retention and ownership policy outside the repo if your org requires formal compliance signoff.

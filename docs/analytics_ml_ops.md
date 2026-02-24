# Predictive Model Ops

This runbook covers scheduled training, quality monitoring, and drift handling for ML-backed predictive alerts.

## Scheduled Workflow

- Workflow: `.github/workflows/predictive_model_ops.yml`
- Schedule: daily (`cron: 10 5 * * *`)
- DB secret: `ANALYTICS_DATABASE_URL`

Each run:
1. Trains seasonal models (`seasonal_hourly_v1`) for all projects.
2. Evaluates active models over a recent window.
3. Persists quality rows to `predictive_model_quality`.
4. Uploads `artifacts/predictive_model_ops.json`.

## Manual Run

Use workflow dispatch inputs to tune windows and thresholds:
- `days`
- `min_events`
- `eval_hours`
- `min_samples`
- `drift_ratio_threshold`
- `mae_threshold`
- `strict`
- `disable_drifted`

## Local CLI

```bash
py -3.11 scripts/predictive_model_ops.py --json-out artifacts/predictive_model_ops.json
```

Strict mode (exit non-zero when drift is detected):

```bash
py -3.11 scripts/predictive_model_ops.py --strict
```

## API Monitoring

- `POST /projects/{project_id}/analytics/predictive/quality/run`
- `GET /projects/{project_id}/analytics/predictive/quality`

These endpoints provide explicit drift/quality visibility per model/check.

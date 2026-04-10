# Deployment Guide

This project now includes a real staging/production deployment path built around:

- environment validation via `scripts/validate_env.py`
- a Kubernetes Helm chart in `deploy/helm/lastping`
- a manual GitHub Actions workflow in `.github/workflows/deploy.yml`
- GitHub Environments for staging / production approvals and secrets

## Recommended runtime shape

For deployed environments, treat LastPing as:

- `api` deployment: FastAPI / Uvicorn
- `worker` deployment(s): background scanning, alerting, remediation, retention
- external PostgreSQL
- external Redis
- ingress / load balancer in front of the API

The Helm chart assumes PostgreSQL and Redis are managed outside the chart and injected through secrets.

## GitHub Environment setup

Create two GitHub Environments:

- `staging`
- `production`

Recommended protected settings:

- required reviewers for `production`
- restricted branch or tag deployment rules

### Required environment secrets

- `DEPLOY_DATABASE_URL`
- `DEPLOY_BASE_URL`
- `DEPLOY_ADMIN_TOKEN`
- `KUBE_CONFIG_B64`

### Required production secret

- `DEPLOY_REDIS_URL`

### Optional environment secrets

- `DEPLOY_SLACK_WEBHOOK_URL`
- `DEPLOY_DISCORD_WEBHOOK_URL`
- `DEPLOY_TWILIO_ACCOUNT_SID`
- `DEPLOY_TWILIO_AUTH_TOKEN`
- `DEPLOY_TWILIO_FROM`
- `DEPLOY_OTEL_EXPORTER_OTLP_HEADERS`

### Required environment variables

- `LASTPING_IMAGE_REPOSITORY`
- `LASTPING_INGRESS_HOST`

### Optional environment variable

- `LASTPING_NAMESPACE`
  Default: `lastping-staging` or `lastping-production`
- `DEPLOY_OTEL_SERVICE_NAME`
  Default: `lastping-api`
- `DEPLOY_OTEL_SERVICE_NAMESPACE`
  Default: `lastping`
- `DEPLOY_OTEL_EXPORTER_OTLP_ENDPOINT`
  Recommended for staging and production if you run an OpenTelemetry collector.
- `DEPLOY_OTEL_METRIC_EXPORT_INTERVAL`
  Default: `15000` ms

## Validate config locally

Use the example env files:

- `deploy/env/staging.env.example`
- `deploy/env/production.env.example`

Run:

```bash
python scripts/validate_env.py --profile staging --env-file deploy/env/staging.env.example
python scripts/validate_env.py --profile production --env-file deploy/env/production.env.example
```

The validator checks:

- required keys for staging vs production
- Postgres vs SQLite suitability
- HTTPS `BASE_URL` in production
- Redis URL format
- partial Twilio config
- malformed webhook URLs
- malformed OTLP collector URLs and missing `OTEL_SERVICE_NAME` when OTLP export is enabled

## OTLP collector export

LastPing can export real traces and metrics over OTLP/HTTP to an OpenTelemetry collector.

Runtime knobs:

- `OTEL_SERVICE_NAME`
- `OTEL_SERVICE_NAMESPACE`
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
- `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`
- `OTEL_EXPORTER_OTLP_HEADERS`
- `OTEL_EXPORTER_OTLP_TRACES_HEADERS`
- `OTEL_EXPORTER_OTLP_METRICS_HEADERS`
- `OTEL_EXPORTER_OTLP_TIMEOUT`
- `OTEL_METRIC_EXPORT_INTERVAL`

If only `OTEL_EXPORTER_OTLP_ENDPOINT` is set, LastPing derives:

- traces -> `/v1/traces`
- metrics -> `/v1/metrics`

Example:

```bash
OTEL_SERVICE_NAME=lastping-api
OTEL_SERVICE_NAMESPACE=lastping
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.staging.internal:4318
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer your-token
OTEL_METRIC_EXPORT_INTERVAL=15000
```

You can verify runtime wiring through:

- `GET /observability/prometheus`
- `GET /observability/otel/config`
- `GET /observability/otel/traces`

## Manual deploy flow

1. Push the image you want to deploy to your container registry.
2. Open GitHub Actions -> `Deploy`.
3. Choose:
   - `environment`: `staging` or `production`
   - `image_tag`: the image tag to roll out
4. The workflow will:
   - validate the environment secrets / vars
   - render a runtime env file
   - run `scripts/validate_env.py`
   - lint the Helm chart
   - update the Kubernetes secret `lastping-env`
   - run `helm upgrade --install`
   - wait for rollouts
   - hit `${BASE_URL}/health`

## Helm chart layout

- `deploy/helm/lastping/values-staging.yaml`
  Single API replica and one worker by default.
- `deploy/helm/lastping/values-production.yaml`
  Two API replicas plus three regional worker deployments with failover env values.

Override image and ingress host at deploy time using workflow inputs / environment vars.

## Rollback

Check release history:

```bash
helm history lastping -n lastping-production
```

Rollback:

```bash
helm rollback lastping <REVISION> -n lastping-production
```

Then verify:

```bash
kubectl get deploy -n lastping-production
curl -fsS https://status.lastping.example.com/health
```

## Post-deploy checks

- `GET /health`
- worker pods are healthy
- dashboard loads
- public status page loads
- background alerts / retention / model ops are not erroring

For multi-region validation, continue to use:

- `multi_region.md`
- `docs/multi_region_validation.md`

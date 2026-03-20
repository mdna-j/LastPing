# Profiling Baseline

- checks_seeded: `30`
- events_per_check: `20`
- retention_rows_per_check: `20`
- repeat: `3`

| Scenario | Avg ms | Total s |
|---|---:|---:|
| `raw_retention_prune_archive_direct` | `191.68` | `0.575` |
| `worker_scan_once` | `118.81` | `0.3564` |
| `metrics_availability` | `51.18` | `0.1536` |
| `raw_retention_prune_direct` | `44.34` | `0.133` |
| `analytics_trends` | `38.58` | `0.1158` |
| `incidents_list` | `35.24` | `0.1057` |
| `metrics_availability_direct` | `30.16` | `0.0905` |
| `ui_dashboard_health` | `7.46` | `0.0224` |
| `analytics_trends_direct` | `7.03` | `0.0211` |

## Direct vs HTTP

| HTTP scenario | Direct scenario | HTTP avg ms | Direct avg ms | HTTP overhead ms |
|---|---|---:|---:|---:|
| `metrics_availability` | `metrics_availability_direct` | `51.18` | `30.16` | `21.02` |
| `analytics_trends` | `analytics_trends_direct` | `38.58` | `7.03` | `31.55` |

## Retention Overhead

| Baseline scenario | Archive scenario | Baseline avg ms | Archive avg ms | Archive overhead ms |
|---|---|---:|---:|---:|
| `raw_retention_prune_direct` | `raw_retention_prune_archive_direct` | `44.34` | `191.68` | `147.34` |

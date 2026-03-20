# Profiling Baseline

- checks_seeded: `30`
- events_per_check: `20`
- retention_rows_per_check: `20`
- repeat: `3`

| Scenario | Avg ms | Total s |
|---|---:|---:|
| `raw_retention_prune_archive_direct` | `243.81` | `0.7314` |
| `worker_scan_once` | `149.7` | `0.4491` |
| `metrics_availability` | `49.19` | `0.1476` |
| `raw_retention_prune_direct` | `44.33` | `0.133` |
| `analytics_trends` | `36.32` | `0.109` |
| `incidents_list` | `31.62` | `0.0949` |
| `metrics_availability_direct` | `30.67` | `0.092` |
| `ui_dashboard_health` | `6.28` | `0.0188` |
| `analytics_trends_direct` | `5.85` | `0.0175` |

## Direct vs HTTP

| HTTP scenario | Direct scenario | HTTP avg ms | Direct avg ms | HTTP overhead ms |
|---|---|---:|---:|---:|
| `metrics_availability` | `metrics_availability_direct` | `49.19` | `30.67` | `18.52` |
| `analytics_trends` | `analytics_trends_direct` | `36.32` | `5.85` | `30.47` |

## Retention Overhead

| Baseline scenario | Archive scenario | Baseline avg ms | Archive avg ms | Archive overhead ms |
|---|---|---:|---:|---:|
| `raw_retention_prune_direct` | `raw_retention_prune_archive_direct` | `44.33` | `243.81` | `199.48` |

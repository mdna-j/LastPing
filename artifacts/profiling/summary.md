# Profiling Baseline

- checks_seeded: `40`
- events_per_check: `25`
- repeat: `3`

| Scenario | Avg ms | Total s |
|---|---:|---:|
| `worker_scan_once` | `135.89` | `0.4077` |
| `metrics_availability` | `55.41` | `0.1662` |
| `metrics_availability_direct` | `41.32` | `0.124` |
| `analytics_trends` | `34.47` | `0.1034` |
| `incidents_list` | `31.9` | `0.0957` |
| `analytics_trends_direct` | `9.31` | `0.0279` |
| `ui_dashboard_health` | `7.5` | `0.0225` |

## Direct vs HTTP

| HTTP scenario | Direct scenario | HTTP avg ms | Direct avg ms | HTTP overhead ms |
|---|---|---:|---:|---:|
| `metrics_availability` | `metrics_availability_direct` | `55.41` | `41.32` | `14.09` |
| `analytics_trends` | `analytics_trends_direct` | `34.47` | `9.31` | `25.16` |

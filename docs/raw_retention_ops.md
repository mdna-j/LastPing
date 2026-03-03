# Raw Retention Ops Runbook

This runbook covers operational tuning for high-volume raw tables (`event`, `checkresult`, `anomaly`, `heartbeat`) when retention jobs run in production.

## What The Worker Does

The worker retention path performs:

- Time-based retention cutoffs per table (`RAW_RETENTION_*_DAYS`)
- Chunked deletes (`RAW_RETENTION_DELETE_BATCH_SIZE`)
- Per-table batch caps (`RAW_RETENTION_MAX_BATCHES_PER_TABLE`)
- Optional pacing between chunks (`RAW_RETENTION_BATCH_PAUSE_MS`)
- Optional archive-before-delete to NDJSON (`RAW_RETENTION_ARCHIVE_ENABLED`, `RAW_RETENTION_ARCHIVE_DIR`)
- Audit log output (`raw_retention_pruned`) with deleted/archived row counts and truncation signals

If chunk caps are hit, the worker logs a warning and marks `truncated_tables` in audit details.

## Recommended Defaults

Start conservative, then tune upward:

- `RAW_RETENTION_DELETE_BATCH_SIZE=5000`
- `RAW_RETENTION_MAX_BATCHES_PER_TABLE=200`
- `RAW_RETENTION_BATCH_PAUSE_MS=50` (for busy primaries)
- `RAW_RETENTION_INTERVAL_SECONDS=86400`
- Enable archive in non-dev environments:
  - `RAW_RETENTION_ARCHIVE_ENABLED=1`
  - `RAW_RETENTION_ARCHIVE_DIR=/var/lib/lastping/retention_archive`

## Tuning Playbook

1. Check `raw_retention_pruned` audit logs for:
   - `truncated_tables`
   - per-table `batches` and `deleted` counts
2. If truncation appears repeatedly:
   - Increase `RAW_RETENTION_MAX_BATCHES_PER_TABLE`
   - Increase `RAW_RETENTION_DELETE_BATCH_SIZE` gradually
   - Add a small `RAW_RETENTION_BATCH_PAUSE_MS` if DB latency spikes
3. If retention still cannot catch up:
   - Move to DB-native partitioning (below)

## Partitioning Strategy (Postgres)

For sustained high write rates:

1. Partition raw tables by time (`created_at` / `timestamp`) monthly or weekly.
2. Keep indexes local to partitions for `check_id` and timestamp.
3. Prune by dropping old partitions instead of deleting rows.
4. Keep the worker chunked-delete path as a safe fallback for non-partitioned environments.

Example approach:

- Parent partitioned table per raw model
- Child partitions like `event_2026_01`, `event_2026_02`
- Scheduled job: drop partitions older than retention policy

## Offline Archival Strategy

Archive files are NDJSON snapshots written before deletion when enabled.

Operational guidance:

- Use durable storage (attached volume, object-storage sync job, or backup mount)
- Rotate/compress NDJSON files daily
- Encrypt archive at rest if it can contain sensitive payloads
- Add checksums/manifest if archives are part of compliance evidence

## Validation Checklist

- [ ] Retention runs on schedule and writes audit logs
- [ ] No recurring `truncated_tables` for more than 2 consecutive runs
- [ ] Archive directory is writable and monitored
- [ ] Archived row count matches deleted row count for archived tables
- [ ] Restore drill: sample NDJSON can be replayed/read during incident review

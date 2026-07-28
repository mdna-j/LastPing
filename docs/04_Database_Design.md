# Database Design

**Project Name:** LastPing
**Document Version:** 2.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## Table of Contents

1. Introduction
2. Design Goals
3. Technology Rationale
4. Entity Relationship Overview
5. Enumerations
6. Table Definitions
7. Indexes
8. Constraints and Referential Integrity
9. Data Volume Estimates
10. Schema Evolution Strategy

---

## 1. Introduction

This document defines the relational database schema for LastPing version 1.0. The schema is implemented in PostgreSQL and accessed exclusively through SQLModel repository classes defined in `persistence/repositories.py`. No other layer issues raw SQL or touches a database session directly.

The schema is designed to satisfy the data collection requirements defined in the SRS (FR-011 through FR-018), the analytics query requirements (FR-019 through FR-026), and the non-functional requirements around performance (NFR-002, NFR-003) and extensibility (NFR-013).

---

## 2. Design Goals

- **Normalization**: service configuration, check results, incidents, and alert channels are stored in separate tables to avoid duplication and support independent querying.
- **Operational state on the service**: `current_status`, `last_check_at`, and `last_success_at` are maintained directly on the `service` table so the dashboard can load all service health with a single query rather than scanning `check_result` for every service on every refresh.
- **Incidents as first-class entities**: outages are represented as `incident` records rather than inferred from sequences of failed `check_result` rows, enabling MTTR, MTTF, and outage duration analytics directly.
- **Query performance**: the highest-volume table (`check_result`) is indexed on `(service_id, timestamp)` to support both the dashboard and the analytics engine. Pre-aggregated `daily_metric` rows prevent full table scans for trend queries.
- **Extensibility**: adding a new monitoring protocol requires only that new check types produce a `check_result` compatible with the existing schema. Adding a new notification channel requires only a new row in `notification_channel` rather than a schema change.
- **Auditability**: every check result, incident, and alert dispatched is persisted indefinitely unless explicitly purged by the user.

---

## 3. Technology Rationale

### 3.1 Why PostgreSQL

PostgreSQL was selected because it provides strong ACID guarantees, mature indexing support including partial and composite indexes, excellent time-series query performance for the append-only `check_result` table, native enum types for data integrity, and JSON support for future extensibility of notification channel configuration. PostgreSQL also integrates cleanly with SQLModel via SQLAlchemy and is widely available for local development and future cloud deployment.

### 3.2 Why UUID Primary Keys

UUIDs are used as primary keys instead of sequential integer identifiers for two reasons. First, they simplify future synchronization between multiple monitoring nodes or data import/export operations by avoiding identifier collisions. Second, they allow client-side ID generation, which reduces round-trips when inserting records from background async tasks.

---

## 4. Entity Relationship Overview

```
service ──────────────────┬──── check_result
   │                      │         (one service, many results)
   │                      │
   │                      ├──── incident
   │                      │         (one service, many incidents)
   │                      │
   │                      ├──── notification_channel
   │                      │         (one service, many channels)
   │                      │
   │                      └──── daily_metric
   │                                (one service, many daily rows)
   │
   └──── settings (global, one row)
```

- A `service` record represents a single monitored endpoint and carries its current operational state.
- Each monitoring check produces one `check_result` record.
- Each detected outage produces one `incident` record, updated when the service recovers.
- Each service has one or more `notification_channel` records defining where alerts are sent.
- Nightly aggregation produces one `daily_metric` row per service per day.
- A single `settings` row stores global application configuration.

---

## 5. Enumerations

PostgreSQL native enums are used for all categorical columns to enforce data integrity at the database level and simplify analytics queries.

```sql
CREATE TYPE monitor_type AS ENUM ('http', 'https', 'tcp', 'dns');

CREATE TYPE service_status AS ENUM ('healthy', 'degraded', 'down', 'paused', 'unknown');
-- Used for both service.current_status and check_result.status

CREATE TYPE failure_type AS ENUM (
    'timeout',
    'connection_refused',
    'dns_resolution',
    'ssl_error',
    'http_error',
    'authentication',
    'unknown'
);

CREATE TYPE channel_type AS ENUM ('desktop', 'email', 'discord', 'slack', 'webhook');
```

Using enums rather than `VARCHAR` check constraints means:
- Invalid values are rejected at the database level, not just the application level.
- Analytics queries can group and count by failure type without string parsing.
- New enum values can be added with `ALTER TYPE ... ADD VALUE` without breaking existing rows.

---

## 6. Table Definitions

### 6.1 `service`

Stores configuration and current operational state for each monitored service. Operational state columns (`current_status`, `last_check_at`, `last_success_at`) are updated in-place after each check, allowing the dashboard to load all service health with a single `SELECT * FROM service` rather than joining to `check_result`.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| name | VARCHAR(255) | NOT NULL | — | User-defined display name |
| type | monitor_type | NOT NULL | — | Protocol enum: `http`, `https`, `tcp`, `dns` |
| host | VARCHAR(1024) | NOT NULL | — | Hostname or IP address of the monitored target |
| port | INTEGER | NULL | — | Port number; NULL for DNS checks and HTTP checks using default ports |
| path | VARCHAR(2048) | NULL | — | URL path for HTTP/HTTPS checks; NULL for TCP and DNS checks |
| interval_seconds | INTEGER | NOT NULL | 60 | How often to run a check, in seconds |
| timeout_seconds | INTEGER | NOT NULL | 10 | Maximum time to wait for a response |
| retry_count | INTEGER | NOT NULL | 3 | Consecutive failures before marking service down |
| is_paused | BOOLEAN | NOT NULL | FALSE | Whether monitoring is currently paused |
| expected_status_code | INTEGER | NULL | 200 | Expected HTTP status code for HTTP/HTTPS checks |
| expected_response_contains | TEXT | NULL | — | Optional substring expected in the HTTP response body |
| follow_redirects | BOOLEAN | NOT NULL | TRUE | Whether to follow HTTP redirects |
| verify_ssl | BOOLEAN | NOT NULL | TRUE | Whether to validate SSL certificates for HTTPS checks |
| expected_dns_resolution | VARCHAR(512) | NULL | — | Expected IP address for DNS checks |
| current_status | service_status | NOT NULL | 'unknown' | Current operational state |
| consecutive_failures | INTEGER | NOT NULL | 0 | Number of consecutive failed checks since last success |
| next_run_at | TIMESTAMPTZ | NULL | — | When the scheduler will next execute a check |
| last_run_at | TIMESTAMPTZ | NULL | — | When the scheduler last executed a check |
| deleted_at | TIMESTAMPTZ | NULL | — | Soft delete timestamp; NULL if the service is active |
| last_check_at | TIMESTAMPTZ | NULL | — | Timestamp of the most recent check |
| last_success_at | TIMESTAMPTZ | NULL | — | Timestamp of the most recent successful check |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | When the service was added |
| updated_at | TIMESTAMPTZ | NOT NULL | NOW() | Last configuration change |

**Constraints:**
- `interval_seconds` >= 10
- `timeout_seconds` >= 1 and < `interval_seconds`
- `retry_count` >= 0

---

### 6.2 `check_result`

Stores the outcome of every individual monitoring check. This is the highest-volume table in the schema. All writes are append-only; records are never updated after insert.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| service_id | UUID | NOT NULL | — | Foreign key to `service.id` |
| started_at | TIMESTAMPTZ | NOT NULL | NOW() | When the check request was initiated |
| finished_at | TIMESTAMPTZ | NOT NULL | — | When the check completed or timed out |
| status | service_status | NOT NULL | — | Result state: `healthy`, `degraded`, or `down` |
| response_time_ms | DOUBLE PRECISION | NULL | — | Round-trip time in milliseconds; NULL if no response received |
| status_code | INTEGER | NULL | — | HTTP status code; NULL for TCP and DNS checks |
| response_size_bytes | INTEGER | NULL | — | Size of the HTTP response body in bytes; NULL for TCP and DNS checks |
| content_type | VARCHAR(255) | NULL | — | HTTP Content-Type header value; NULL for TCP and DNS checks |
| failure_type | failure_type | NULL | — | Categorical failure classification; NULL if check succeeded |
| failure_message | TEXT | NULL | — | Human-readable failure detail; NULL if check succeeded |

**Notes:**
- `started_at` and `finished_at` replace a single `timestamp` column, enabling exact execution duration calculation (`finished_at - started_at`) and distinguishing queue delay from actual check runtime.
- `status` uses the `service_status` enum (`healthy`, `degraded`, `down`) rather than a boolean `success` field. A `degraded` result represents a check that technically succeeded but exceeded a response time threshold, capturing the case where `HTTP 200` with a 12-second response time is not actually healthy.
- `DOUBLE PRECISION` is used for `response_time_ms` to preserve sub-millisecond precision for fast local services.
- `response_size_bytes` and `content_type` enable payload analytics such as average response size and unexpected content type detection.
- Splitting failure information into `failure_type` (enum) and `failure_message` (text) allows analytics to answer "what is the most common failure type?" without string parsing, while still preserving the full error context.

---

### 6.3 `incident`

Stores each detected outage as a first-class entity. An incident record is created when a service transitions to `down` and updated when it recovers. This allows outage-level analytics (MTTR, MTTF, longest outage, average duration) without scanning and grouping sequences of `check_result` rows.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| service_id | UUID | NOT NULL | — | Foreign key to `service.id` |
| started_at | TIMESTAMPTZ | NOT NULL | — | When the first failed check was recorded |
| resolved_at | TIMESTAMPTZ | NULL | — | When the service recovered; NULL if still ongoing |
| duration_seconds | INTEGER | NULL | — | Computed on resolution: `resolved_at - started_at` |
| failure_type | failure_type | NULL | — | Dominant failure type observed during the incident |
| checks_failed | INTEGER | NOT NULL | 0 | Total number of failed checks during the incident |

---

### 6.4 `notification_channel`

Stores per-service notification channel configuration. Each row represents one channel (desktop, email, Discord, etc.) for one service. This replaces a fixed set of boolean columns (`desktop_enabled`, `email_enabled`, `discord_enabled`) with a flexible, extensible design that supports adding new channel types (Slack, Teams, SMS, PagerDuty) without a schema change.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| service_id | UUID | NOT NULL | — | Foreign key to `service.id` |
| type | channel_type | NOT NULL | — | Channel type enum |
| enabled | BOOLEAN | NOT NULL | TRUE | Whether this channel is currently active |
| configuration | JSONB | NULL | — | Channel-specific config (e.g., webhook URL, email address) |
| threshold | INTEGER | NOT NULL | 3 | Consecutive failures before this channel fires |
| is_snoozed | BOOLEAN | NOT NULL | FALSE | Whether alerts from this channel are temporarily muted |
| snooze_until | TIMESTAMPTZ | NULL | — | When the snooze expires; NULL if not snoozed |

**Notes:**
- `configuration` uses JSONB to store channel-specific fields (e.g., `{"webhook_url": "..."}` for Discord, `{"to": "..."}` for email) without requiring separate tables per channel type.
- A `UNIQUE(service_id, type)` constraint prevents duplicate channel entries of the same type per service.

**Example `configuration` JSONB values by channel type:**

Discord:
```json
{
  "webhook_url": "https://discord.com/api/webhooks/...",
  "username": "LastPing"
}
```

Email:
```json
{
  "recipient": "admin@example.com"
}
```

Slack (future):
```json
{
  "webhook_url": "https://hooks.slack.com/services/...",
  "channel": "#alerts"
}
```

---

### 6.5 `alert_log`

Stores a record of every alert dispatched by the Alerting Engine.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| service_id | UUID | NOT NULL | — | Foreign key to `service.id` |
| timestamp | TIMESTAMPTZ | NOT NULL | NOW() | When the alert was dispatched |
| channel | channel_type | NOT NULL | — | Which channel was used |
| message | TEXT | NOT NULL | — | The alert message content |
| success | BOOLEAN | NOT NULL | — | Whether dispatch succeeded |
| error_detail | VARCHAR(512) | NULL | — | Error detail if dispatch failed |

---

### 6.6 `daily_metric`

Stores pre-aggregated daily statistics per service. One row per service per calendar day, computed by a nightly background job. This prevents the Analytics Engine from scanning potentially millions of `check_result` rows for long-range trend queries, satisfying NFR-003.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | Primary key |
| service_id | UUID | NOT NULL | — | Foreign key to `service.id` |
| date | DATE | NOT NULL | — | The calendar day this row covers |
| total_checks | INTEGER | NOT NULL | 0 | Total checks executed that day |
| successful_checks | INTEGER | NOT NULL | 0 | Total successful checks that day |
| availability_percentage | DOUBLE PRECISION | NOT NULL | 0 | `(successful_checks / total_checks) * 100` |
| avg_response_time_ms | DOUBLE PRECISION | NULL | — | Average response time across successful checks |
| p95_response_time_ms | DOUBLE PRECISION | NULL | — | 95th percentile response time |
| total_incidents | INTEGER | NOT NULL | 0 | Number of incidents that started this day |

**Constraints:**
- `UNIQUE(service_id, date)` — one row per service per day.

---

### 6.7 `settings`

Stores global application configuration as a single row. Centralizing configuration here avoids scattering settings across config files, environment variables, and application code.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| id | INTEGER | NOT NULL | 1 | Always 1; enforced by CHECK constraint |
| theme | VARCHAR(20) | NOT NULL | 'dark' | UI theme: `dark` or `light` |
| retention_days | INTEGER | NOT NULL | 90 | How many days of check results to retain |
| default_interval_seconds | INTEGER | NOT NULL | 60 | Default check interval for new services |
| default_timeout_seconds | INTEGER | NOT NULL | 10 | Default timeout for new services |
| default_retry_count | INTEGER | NOT NULL | 3 | Default retry count for new services |
| smtp_host | VARCHAR(255) | NULL | — | SMTP server hostname for email alerts |
| smtp_port | INTEGER | NULL | 587 | SMTP server port |
| smtp_username | VARCHAR(255) | NULL | — | SMTP account username |
| smtp_password_encrypted | TEXT | NULL | — | Encrypted SMTP password (never stored in plaintext) |
| smtp_from_address | VARCHAR(255) | NULL | — | From address for outgoing alert emails |
| logging_level | VARCHAR(10) | NOT NULL | 'INFO' | Application log verbosity |
| log_file_path | VARCHAR(1024) | NULL | — | Path to the application log file |
| schema_version | INTEGER | NOT NULL | 1 | Current database schema version; used by Alembic migration checks |
| database_version | VARCHAR(20) | NOT NULL | '1.0.0' | Application version that last modified the schema |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | When settings were first initialized |
| updated_at | TIMESTAMPTZ | NOT NULL | NOW() | Last settings change |

**Constraints:**
- `CHECK (id = 1)` — enforces singleton row pattern.
- `logging_level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')`.

---

## 7. Indexes

| Index Name | Table | Columns | Type | Purpose |
|---|---|---|---|---|
| `idx_check_result_service_started` | `check_result` | `(service_id, started_at DESC)` | B-tree | Primary analytics and most-recent-check query path |
| `idx_check_result_success` | `check_result` | `(service_id, success)` | B-tree | Uptime percentage and failure frequency queries |
| `idx_check_result_failure_type` | `check_result` | `(failure_type)` | B-tree | Failure type analytics queries |
| `idx_incident_service_started` | `incident` | `(service_id, started_at DESC)` | B-tree | Incident history per service |
| `idx_incident_unresolved` | `incident` | `(service_id) WHERE resolved_at IS NULL` | Partial B-tree | Fast lookup of ongoing incidents |
| `idx_daily_metric_service_date` | `daily_metric` | `(service_id, date DESC)` | B-tree | Trend chart queries |
| `idx_alert_log_service_timestamp` | `alert_log` | `(service_id, timestamp DESC)` | B-tree | Alert history queries per service |
| `idx_notification_channel_service` | `notification_channel` | `(service_id)` | B-tree | Alert engine channel lookup per service |

The partial index on `incident WHERE resolved_at IS NULL` is particularly efficient for the Alerting Engine's check to determine whether a service is already in an active incident, avoiding a full scan of the incident table.

---

## 8. Constraints and Referential Integrity

| Constraint | Table | Definition |
|---|---|---|
| `fk_check_result_service` | `check_result` | `service_id REFERENCES service(id) ON DELETE CASCADE` |
| `fk_incident_service` | `incident` | `service_id REFERENCES service(id) ON DELETE CASCADE` |
| `fk_notification_channel_service` | `notification_channel` | `service_id REFERENCES service(id) ON DELETE CASCADE` |
| `fk_alert_log_service` | `alert_log` | `service_id REFERENCES service(id) ON DELETE CASCADE` |
| `fk_daily_metric_service` | `daily_metric` | `service_id REFERENCES service(id) ON DELETE CASCADE` |
| `uq_notification_channel_type` | `notification_channel` | `UNIQUE(service_id, type)` |
| `uq_daily_metric_date` | `daily_metric` | `UNIQUE(service_id, date)` |
| `ck_settings_singleton` | `settings` | `CHECK (id = 1)` |
| `ck_service_interval` | `service` | `CHECK (interval_seconds >= 10)` |
| `ck_service_timeout` | `service` | `CHECK (timeout_seconds >= 1)` |

A `deleted_at` column on `service` implements soft deletion. Queries throughout the application filter on `WHERE deleted_at IS NULL` to exclude soft-deleted services from monitoring and the dashboard. Historical `check_result` and `incident` data for soft-deleted services is retained unless explicitly purged.

`ON DELETE CASCADE` on all foreign keys means that deleting a `service` record automatically removes all associated records across every related table, satisfying the service deletion behavior defined in FR-040.

---

## 9. Data Volume Estimates

| Scenario | Services | Interval | Checks/Day | check_result Rows/Year |
|---|---|---|---|---|
| Light use | 5 | 60s | 7,200 | ~2.6M |
| Moderate use | 20 | 60s | 28,800 | ~10.5M |
| Target maximum (NFR-001) | 50 | 60s | 72,000 | ~26.3M |

At the target maximum of 50 services checking every 60 seconds, `check_result` grows at approximately 72,000 rows per day. At an estimated average row size of 120 bytes, this is approximately 8.6 MB per day or 3.1 GB per year before purging.

`daily_metric` grows at 50 rows per day regardless of check frequency, adding negligible storage overhead while enabling instant trend chart queries without scanning `check_result`.

The user-configurable data purge (FR-048) governed by `settings.retention_days` is the primary mechanism for managing `check_result` table size over time.

---

## 10. Schema Evolution Strategy

The schema is versioned using Alembic, which is included as a development dependency. Migration scripts are stored in `migrations/versions/` and applied automatically on application startup if the schema is behind the current version.

Guidelines for future schema changes:

- Adding a new monitoring protocol requires updating the `monitor_type` enum with `ALTER TYPE ... ADD VALUE` and adding a new `Checker` implementation. No existing tables or queries are affected.
- Adding a new notification channel requires updating the `channel_type` enum and adding a new channel adapter in `alerting/channels/`. No schema change is needed beyond the enum addition.
- SSL certificate monitoring would introduce a new result field on `check_result` or a separate `ssl_check_result` table depending on whether the data volume justifies separation.
- Distributed monitoring agents would require a `monitoring_node` table and a foreign key on `check_result` to track which node produced each result. The anticipated future ER relationship would be:

```
service
  └── check_result
        └── monitoring_node
```

- A `service_runtime` table could eventually split operational state (`current_status`, `last_check_at`, `last_success_at`) out of the `service` table as the number of monitored services grows and write contention on the `service` table increases. For version 1.0 this split is unnecessary.
- Materialized aggregate tables beyond `daily_metric` (e.g., `weekly_metric`, `monthly_metric`) can be introduced independently without touching the existing schema.

---

*This schema reflects the version 1.0 design. As the application evolves through its roadmap, this document should be updated to reflect any migrations applied and any new design decisions made.*
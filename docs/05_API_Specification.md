# API Specification

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## Table of Contents

1. Introduction
2. Part I — Internal Service Layer API
   - 2.1 Overview
   - 2.2 Conventions
   - 2.3 Data Models
   - 2.4 Service Management
   - 2.5 Monitoring Control
   - 2.6 Analytics
   - 2.7 Alert Configuration
   - 2.8 Settings
   - 2.9 Data Management
3. Part II — Future REST API
   - 3.1 Overview
   - 3.2 Conventions
   - 3.3 Authentication
   - 3.4 API Versioning
   - 3.5 Services
   - 3.6 Check Results
   - 3.7 Incidents
   - 3.8 Analytics
   - 3.9 Alerts
   - 3.10 Settings
   - 3.11 Error Responses

---

## 1. Introduction

This document specifies the API surface of LastPing in two parts.

**Part I** defines the internal Service Layer API — the Python method signatures exposed by `ApplicationService` in `service_layer/application_service.py`. This is the only interface the PySide6 UI layer is permitted to call. It represents the real API of version 1.0, a single-user desktop application with no HTTP server component.

**Part II** defines a speculative REST API that a future version of LastPing could expose to support web dashboards, remote monitoring agents, CLI clients, or third-party integrations. It is written in OpenAPI-compatible style and is intended to demonstrate forward-looking API design thinking rather than describe currently implemented functionality.

---

## Part I — Internal Service Layer API

### 2.1 Overview

The Service Layer is implemented as a single class, `ApplicationService`, in `service_layer/application_service.py`. It is instantiated once at application startup and injected into the UI layer. No other layer is instantiated or imported directly by the UI.

All methods are async (`async def`) and must be awaited by the UI. Long-running operations that produce progress updates use callbacks or Qt signals rather than blocking return values.

The Service Layer is the single seam between the UI and all backend logic. This means:

- The UI never touches `CheckResultRepository`, `AnalyticsEngine`, `AlertingEngine`, or `APScheduler` directly.
- All validation of user input happens inside the Service Layer before it reaches the Persistence or Monitoring layers.
- Unit tests for business logic target `ApplicationService` in isolation from PySide6.

---

### 2.2 Conventions

- All identifiers are UUIDs represented as Python `uuid.UUID` objects.
- Timestamps are `datetime` objects with UTC timezone.
- Methods raise typed exceptions from `service_layer/exceptions.py` rather than returning error codes.
- Methods that return lists return empty lists rather than `None` when no results are found.
- Methods that return a single item raise `ServiceNotFoundError` or equivalent rather than returning `None`.

**Exception types:**

| Exception | When raised |
|---|---|
| `ServiceNotFoundError` | Requested service UUID does not exist or is soft-deleted |
| `ValidationError` | Input fails validation (invalid URL, interval too short, etc.) |
| `SchedulerError` | APScheduler fails to add, pause, or remove a job |
| `PersistenceError` | Database write fails after retries |
| `ChannelDispatchError` | Notification channel fails to send |

---

### 2.3 Data Models

These are the typed return models used across Service Layer methods. They are implemented as Pydantic models or Python dataclasses.

#### `ServiceSummary`
Returned by list and dashboard queries. Contains operational state but not full configuration.

```python
@dataclass
class ServiceSummary:
    id: uuid.UUID
    name: str
    type: MonitorType          # Enum: http | https | tcp | dns
    host: str
    current_status: ServiceStatus  # Enum: healthy | degraded | down | paused | unknown
    last_check_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    uptime_percentage_24h: float | None
```

#### `ServiceDetail`
Returned by single-service queries. Full configuration and runtime state.

```python
@dataclass
class ServiceDetail:
    id: uuid.UUID
    name: str
    type: MonitorType
    host: str
    port: int | None
    path: str | None
    interval_seconds: int
    timeout_seconds: int
    retry_count: int
    is_paused: bool
    deleted_at: datetime | None
    expected_status_code: int | None
    expected_response_contains: str | None
    follow_redirects: bool
    verify_ssl: bool
    current_status: ServiceStatus
    consecutive_failures: int
    last_check_at: datetime | None
    last_success_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

#### `CheckResultRecord`

```python
@dataclass
class CheckResultRecord:
    id: uuid.UUID
    service_id: uuid.UUID
    started_at: datetime
    finished_at: datetime
    status: ServiceStatus
    response_time_ms: float | None
    status_code: int | None
    response_size_bytes: int | None
    content_type: str | None
    duration_ms: float          # Computed: (finished_at - started_at).total_seconds() * 1000
    failure_type: FailureType | None
    failure_message: str | None
```

#### `OperationResult`
Returned by mutating operations (delete, pause, resume, import) to provide structured feedback for UI status messages rather than returning `None`.

```python
@dataclass
class OperationResult:
    success: bool
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

**Example — delete_service result:**
```python
OperationResult(
    success=True,
    message="Service deleted successfully.",
    metadata={
        "scheduler_removed": True,
        "history_retained": True,
        "rows_deleted": 0
    }
)
```

**Example — import_service_config result:**
```python
OperationResult(
    success=True,
    message="3 services imported successfully.",
    metadata={
        "imported": 3,
        "skipped": 1,
        "skipped_reason": "Service with matching name already exists"
    }
)
```

---

#### `IncidentRecord`

```python
@dataclass
class IncidentRecord:
    id: uuid.UUID
    service_id: uuid.UUID
    started_at: datetime
    resolved_at: datetime | None
    duration_seconds: int | None
    failure_type: FailureType | None
    checks_failed: int
```

#### `ServiceAnalytics`

```python
@dataclass
class ServiceAnalytics:
    service_id: uuid.UUID
    date_range_start: datetime
    date_range_end: datetime
    total_checks: int
    successful_checks: int
    availability_percentage: float
    avg_response_time_ms: float | None
    p95_response_time_ms: float | None
    total_incidents: int
    avg_incident_duration_seconds: float | None
    longest_incident_seconds: int | None
    mttr_seconds: float | None       # Mean Time To Recovery
    mtbf_seconds: float | None       # Mean Time Between Failures
    # Future fields (planned for v1.x):
    # availability_trend: list[tuple[date, float]]   # Daily availability % over range
    # response_time_trend: list[tuple[date, float]]  # Daily avg response time over range
    # failure_distribution: dict[FailureType, int]   # Count per failure type
```

---

### 2.4 Service Management

#### `get_all_services() -> list[ServiceSummary]`
Returns all active (non-deleted) services with their current operational state. Used to populate the dashboard.

#### `get_service(service_id: UUID) -> ServiceDetail`
Returns full configuration and runtime state for a single service.
Raises `ServiceNotFoundError` if the service does not exist or is soft-deleted.

#### `add_service(config: ServiceCreateRequest) -> ServiceDetail`
Validates the configuration, persists a new service record, creates a default `notification_channel` row for desktop notifications, and registers the service with the scheduler.
Raises `ValidationError` if the configuration is invalid.

```python
@dataclass
class ServiceCreateRequest:
    name: str
    type: MonitorType
    host: str
    port: int | None = None
    path: str | None = None
    interval_seconds: int = 60
    timeout_seconds: int = 10
    retry_count: int = 3
    expected_status_code: int | None = 200
    expected_response_contains: str | None = None
    follow_redirects: bool = True
    verify_ssl: bool = True
```

#### `update_service(service_id: UUID, update: ServiceUpdateRequest) -> ServiceDetail`
Updates the configuration of an existing service. If `interval_seconds` changes, reschedules the APScheduler job. Raises `ServiceNotFoundError` or `ValidationError`.

#### `delete_service(service_id: UUID, retain_history: bool = True) -> OperationResult`
Soft-deletes the service by setting `deleted_at`. Removes its job from the scheduler. If `retain_history=False`, also hard-deletes all associated `check_result`, `incident`, and `alert_log` records. Returns an `OperationResult` with metadata about what was removed, enabling the UI to display a meaningful confirmation message.

---

### 2.5 Monitoring Control

#### `pause_service(service_id: UUID) -> None`
Pauses monitoring for the service. Sets `is_paused = True` and `current_status = 'paused'`. Suspends the APScheduler job without removing it.

#### `resume_service(service_id: UUID) -> None`
Resumes monitoring. Sets `is_paused = False`. Resumes the APScheduler job and immediately schedules a check.

#### `trigger_check(service_id: UUID) -> CheckResultRecord`
Executes an immediate out-of-schedule check and returns the result. Does not affect the normal check schedule.

---

### 2.6 Analytics

#### `get_analytics(service_id: UUID, start: datetime, end: datetime) -> ServiceAnalytics`
Returns aggregated analytics for a service over the specified date range. For ranges covered by `daily_metric` rows, aggregates from pre-computed daily data. For partial days, queries `check_result` directly.

#### `get_check_history(service_id: UUID, start: datetime, end: datetime, limit: int = 500) -> list[CheckResultRecord]`
Returns individual check results for a service within the date range, ordered by `started_at` descending. Limited to `limit` records per call to prevent large memory allocations.

#### `get_incident_history(service_id: UUID, start: datetime, end: datetime) -> list[IncidentRecord]`
Returns all incidents for a service within the date range, ordered by `started_at` descending.

#### `export_analytics_csv(service_id: UUID, start: datetime, end: datetime, file_path: str) -> None`
Exports check results for the service and date range to a CSV file at `file_path`. Implements FR-026.

---

### 2.7 Alert Configuration

#### `get_notification_channels(service_id: UUID) -> list[NotificationChannelRecord]`
Returns all configured notification channels for a service.

#### `add_notification_channel(service_id: UUID, channel: ChannelCreateRequest) -> NotificationChannelRecord`
Adds a new notification channel to a service. Raises `ValidationError` if a channel of the same type already exists for this service.

#### `update_notification_channel(channel_id: UUID, update: ChannelUpdateRequest) -> NotificationChannelRecord`
Updates channel configuration, threshold, or enabled state.

#### `delete_notification_channel(channel_id: UUID) -> None`
Removes a notification channel. The service must retain at least one channel.

#### `snooze_channel(channel_id: UUID, until: datetime) -> None`
Temporarily mutes a notification channel until the specified time.

---

### 2.8 Settings

#### `get_settings() -> SettingsRecord`
Returns the global application settings singleton.

#### `update_settings(update: SettingsUpdateRequest) -> SettingsRecord`
Updates global settings. Changes to `default_interval_seconds` and `default_timeout_seconds` affect only new services, not existing ones.

---

### 2.9 Data Management

#### `purge_history(older_than_days: int) -> OperationResult`
Deletes `check_result` and `alert_log` records older than `older_than_days` days. Does not delete `incident` records. Returns an `OperationResult` with `metadata["rows_deleted"]` set to the total number of removed rows.

#### `restore_service(service_id: UUID) -> ServiceDetail`
Restores a soft-deleted service by clearing `deleted_at`. Re-registers the service with the scheduler. Raises `ServiceNotFoundError` if the service has been hard-deleted or does not exist.

---

#### `export_service_config(file_path: str) -> None`
Exports all service configurations (excluding runtime state and history) to a JSON backup file at `file_path`.

#### `import_service_config(file_path: str) -> OperationResult`
Imports service configurations from a previously exported JSON file. Services are added as new records; existing services with matching names are skipped rather than overwritten. Returns an `OperationResult` with `metadata["imported"]` and `metadata["skipped"]` counts so the UI can display a meaningful summary.

---

## Part II — Future REST API

### 3.1 Overview

This section specifies a REST API that a future version of LastPing could expose to support web dashboards, CLI clients, remote monitoring agents, or third-party integrations. It is not implemented in version 1.0.

The API follows REST conventions with JSON request and response bodies. All endpoints are prefixed with `/api/v1`.

**Base URL (future):** `http://localhost:8080/api/v1`

---

### 3.2 Conventions

- All timestamps are ISO 8601 strings in UTC: `2026-07-01T14:32:00Z`
- All IDs are UUIDs: `"3fa85f64-5717-4562-b3fc-2c963f66afa6"`
- Successful responses return `2xx` status codes.
- Empty list results return `200` with an empty array, not `404`.
- Pagination uses `limit` and `offset` query parameters. A future version may migrate to cursor-based pagination (`next_cursor`) for monitoring endpoints where offset pagination becomes expensive on large datasets (e.g., `check_result` tables with millions of rows).
- Partial updates use `PATCH` with only the fields being changed.

---

### 3.3 API Versioning

The current API version is `v1`, reflected in the URL prefix `/api/v1`. A future version may additionally support content negotiation via the `Accept` header:

```
Accept: application/vnd.lastping.v1+json
```

This would allow clients to pin to a specific API version independently of the URL path, supporting gradual migration when breaking changes are introduced. This is not implemented in the initial REST API release.

---

### 3.4 Authentication

Future versions would use Bearer token authentication. All requests must include:

```
Authorization: Bearer <token>
```

Version 1.0 does not implement authentication as it is a single-user, single-machine application.

---

### 3.5 Services

#### `GET /services`
Returns all active services.

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| status | string | Filter by `healthy`, `degraded`, `down`, `paused` |
| type | string | Filter by `http`, `https`, `tcp`, `dns` |

**Response `200`:**
```json
{
  "services": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "name": "Production API",
      "type": "https",
      "host": "api.example.com",
      "current_status": "healthy",
      "last_check_at": "2026-07-01T14:32:00Z",
      "uptime_percentage_24h": 99.93
    }
  ],
  "total": 1
}
```

---

#### `POST /services`
Creates a new monitored service.

**Request body:**
```json
{
  "name": "Production API",
  "type": "https",
  "host": "api.example.com",
  "port": 443,
  "path": "/health",
  "interval_seconds": 60,
  "timeout_seconds": 10,
  "retry_count": 3,
  "expected_status_code": 200,
  "follow_redirects": true,
  "verify_ssl": true
}
```

**Response `201`:** Returns the created `ServiceDetail` object.

**Response `422`:** Validation error.

```json
{
  "error": "validation_error",
  "message": "interval_seconds must be >= 10",
  "field": "interval_seconds"
}
```

---

#### `GET /services/{service_id}`
Returns full configuration and runtime state for one service.

**Response `200`:** Returns `ServiceDetail`.
**Response `404`:** Service not found.

---

#### `PATCH /services/{service_id}`
Updates service configuration. Only include fields being changed.

**Request body (example — update interval only):**
```json
{
  "interval_seconds": 30
}
```

**Response `200`:** Returns updated `ServiceDetail`.

---

#### `DELETE /services/{service_id}`
Soft-deletes a service and removes it from the scheduler.

**Query parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| retain_history | boolean | true | Whether to keep historical check data |

**Response `204`:** No content.

---

#### `POST /services/{service_id}/pause`
Pauses monitoring for a service.
**Response `200`:** Returns updated `ServiceDetail`.

#### `POST /services/{service_id}/resume`
Resumes monitoring for a service.
**Response `200`:** Returns updated `ServiceDetail`.

#### `POST /services/{service_id}/check`
Triggers an immediate out-of-schedule check.
**Response `200`:** Returns `CheckResultRecord`.

---

### 3.6 Check Results

#### `GET /services/{service_id}/checks`
Returns paginated check history for a service.

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| start | datetime | Start of date range (ISO 8601) |
| end | datetime | End of date range (ISO 8601) |
| status | string | Filter by `healthy`, `degraded`, `down` |
| limit | integer | Max results (default 100, max 1000) |
| offset | integer | Pagination offset |

**Response `200`:**
```json
{
  "checks": [
    {
      "id": "...",
      "service_id": "...",
      "started_at": "2026-07-01T14:32:00Z",
      "finished_at": "2026-07-01T14:32:00.243Z",
      "status": "healthy",
      "response_time_ms": 243.1,
      "duration_ms": 243.1,
      "status_code": 200,
      "response_size_bytes": 1482,
      "content_type": "application/json",
      "failure_type": null,
      "failure_message": null
    }
  ],
  "total": 8640,
  "limit": 100,
  "offset": 0
}
```

---

### 3.7 Incidents

#### `GET /services/{service_id}/incidents`
Returns incident history for a service.

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| start | datetime | Start of date range |
| end | datetime | End of date range |
| resolved | boolean | Filter by resolved/unresolved |

**Response `200`:**
```json
{
  "incidents": [
    {
      "id": "...",
      "service_id": "...",
      "started_at": "2026-07-01T02:14:00Z",
      "resolved_at": "2026-07-01T02:31:00Z",
      "duration_seconds": 1020,
      "failure_type": "timeout",
      "checks_failed": 17
    }
  ],
  "total": 3
}
```

---

### 3.8 Analytics

#### `GET /services/{service_id}/analytics`
Returns aggregated analytics for a service over a date range.

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| start | datetime | Start of date range |
| end | datetime | End of date range |

**Response `200`:**
```json
{
  "service_id": "...",
  "date_range_start": "2026-06-01T00:00:00Z",
  "date_range_end": "2026-07-01T00:00:00Z",
  "total_checks": 43200,
  "successful_checks": 43187,
  "availability_percentage": 99.97,
  "avg_response_time_ms": 187.4,
  "p95_response_time_ms": 412.0,
  "total_incidents": 2,
  "avg_incident_duration_seconds": 780,
  "longest_incident_seconds": 1020,
  "mttr_seconds": 780.0,
  "mtbf_seconds": 1296000.0
}
```

#### `GET /services/{service_id}/analytics/export`
Exports analytics data as a CSV file download.

**Query parameters:** `start`, `end` (same as above)
**Response `200`:** `Content-Type: text/csv` with CSV file attachment.

---

### 3.9 Alerts

#### `GET /services/{service_id}/channels`
Returns notification channels configured for a service.

#### `POST /services/{service_id}/channels`
Adds a notification channel to a service.

**Request body (Discord example):**
```json
{
  "type": "discord",
  "enabled": true,
  "threshold": 3,
  "configuration": {
    "webhook_url": "https://discord.com/api/webhooks/...",
    "username": "LastPing"
  }
}
```

**Request body (Email example):**
```json
{
  "type": "email",
  "enabled": true,
  "threshold": 3,
  "configuration": {
    "recipient": "admin@example.com"
  }
}
```

**Response `201`:** Returns created channel record.

#### `PATCH /channels/{channel_id}`
Updates a notification channel.

#### `DELETE /channels/{channel_id}`
Removes a notification channel.

#### `POST /channels/{channel_id}/snooze`
Snoozes a channel until a specified time.

**Request body:**
```json
{
  "until": "2026-07-01T18:00:00Z"
}
```

#### `GET /services/{service_id}/alerts`
Returns alert dispatch history for a service.

---

### 3.10 Settings

#### `GET /settings`
Returns global application settings.

#### `PATCH /settings`
Updates global settings. Only include fields being changed.

**Request body (example):**
```json
{
  "retention_days": 180,
  "default_interval_seconds": 30
}
```

**Response `200`:** Returns full updated settings object.

---

### 3.11 Error Responses

All error responses follow a consistent structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description of the error.",
  "field": "field_name_if_applicable"
}
```

| HTTP Status | Error Code | When Used |
|---|---|---|
| `400` | `bad_request` | Malformed JSON or missing required fields |
| `404` | `not_found` | Resource does not exist |
| `409` | `conflict` | Duplicate resource (e.g. channel type already exists) |
| `422` | `validation_error` | Input fails business rule validation |
| `500` | `internal_error` | Unexpected server-side error |
| `503` | `scheduler_unavailable` | Monitoring scheduler is not running |

---

*Part I of this document reflects the version 1.0 internal service interface and should be updated as `ApplicationService` evolves. Part II is speculative and subject to significant change when REST API development begins.*
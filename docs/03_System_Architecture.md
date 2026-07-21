# System Architecture Document

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This document describes the software architecture of LastPing, a desktop-based uptime monitoring and service analytics application. It builds on the Project Vision and Software Requirements Specification (SRS) by defining how the system is structured internally: its components, their responsibilities, how they communicate, how data flows through the system, and the technical decisions that support the non-functional requirements defined in the SRS (reliability, maintainability, scalability, and observability).

The intended audience is the developer during implementation, and any reviewer evaluating the project's engineering design.

## 2. Architectural Goals and Constraints

The architecture is driven directly by the design principles in the Project Vision and the constraints in the SRS (Section 10):

- **Reliability**: a slow or failing monitored service must never block or crash the monitoring of other services.
- **Maintainability**: business logic must be independent of the PySide6 UI so it can be tested and reasoned about in isolation.
- **Scalability**: new monitoring protocols (ICMP, SSL certificate checks) must be addable without restructuring the core engine.
- **Observability**: the application must be able to explain its own behavior through logs, not just the behavior of monitored services.
- **Constraints**: Python, PySide6, PostgreSQL via SQLModel, APScheduler, PyQtGraph/Matplotlib, single-user, single-machine, no server component.

These goals lead to a layered architecture with clear boundaries and asynchronous execution at the core.

## 3. High-Level Architecture Overview

LastPing is organized into four cooperating layers, each with a single responsibility. Layers communicate through well-defined interfaces rather than direct cross-layer calls, which keeps the UI replaceable and the monitoring engine testable without a database or GUI present.

```
┌─────────────────────────────────────────────────────────────┐
│                     Desktop Application (UI)                │
│              PySide6 windows, dashboard, charts             │
└───────────────────────────▲─────────────────────────────────┘
                             │ reads/writes via service layer
┌───────────────────────────┴───────────────────────────────────┐
│                        Service / Application Layer            │
│   Orchestrates use cases: add service, run check, get stats   │
└───────┬───────────────────────┬───────────────────┬───────────┘
        │                       │                   │
┌───────▼────────┐   ┌──────────▼─────────┐  ┌──────▼─────────┐
│ Monitoring     │   │ Analytics Engine   │  │ Alerting       │
│ Engine         │   │                    │  │ Engine         │
│ (APScheduler + │   │ Aggregates raw     │  │ Desktop/Email/ │
│ async checks)  │   │ checks into stats  │  │ Discord        │
└───────┬────────┘   └──────────┬─────────┘  └──────┬─────────┘
        │                       │                     │
        └───────────┬───────────┴─────────────────────┘
                     │
             ┌───────▼─────────┐
             │ Persistence     │
             │ Layer           │
             │ SQLModel / PG   │
             └─────────────────┘
```

### Layer Responsibilities

| Layer | Responsibility | Depends On |
|---|---|---|
| Desktop Application (UI) | Renders dashboard, service management, charts, settings screens; forwards user actions to the Service Layer | Service Layer |
| Service / Application Layer | Coordinates use cases (add service, run manual check, fetch analytics); the only layer the UI talks to directly | Monitoring Engine, Analytics Engine, Alerting Engine, Persistence Layer |
| Monitoring Engine | Schedules and executes HTTP/HTTPS/TCP/DNS checks asynchronously; produces raw check results | Persistence Layer |
| Analytics Engine | Reads raw check results and computes uptime percentage, averages, trends, and incident groupings | Persistence Layer |
| Alerting Engine | Evaluates alert thresholds against recent check results and dispatches notifications | Persistence Layer, external channels (SMTP, Discord, OS notification API) |
| Persistence Layer | Provides a typed data access interface over PostgreSQL via SQLModel | PostgreSQL |

This separation directly satisfies NFR-009 and NFR-010 from the SRS: the UI is a thin layer that only calls into the Service Layer, so the monitoring, analytics, and alerting logic can be unit tested with no PySide6 or Qt event loop involved.

## 4. Component Architecture

### 4.1 Monitoring Engine

The Monitoring Engine is responsible for FR-001 through FR-010. Internally it is split into:

- **Scheduler** (APScheduler, `AsyncIOScheduler`): holds one job per monitored service, triggered at that service's configured interval. Jobs are added, paused, resumed, and removed as services are added, paused, or deleted, without restarting the scheduler.
- **Checkers**: one class per protocol implementing a common `Checker` interface (`HttpChecker`, `TcpChecker`, `DnsChecker`). Each returns a normalized `CheckResult` object regardless of protocol, so downstream code never needs to know which protocol produced a result.
- **Retry Handler**: wraps a checker call, retrying up to the service's configured retry count before the result is considered final, satisfying FR-007.
- **Result Publisher**: after a check completes, the engine persists the result and publishes an in-process event (e.g., `service_status_changed`) that the Alerting Engine and the UI's live status widgets subscribe to. This avoids the UI polling the database on a timer.

Because checks run as async tasks under a shared event loop, a slow or hanging TCP check does not block HTTP checks for other services, which satisfies NFR-001 and NFR-004.

### 4.2 Persistence Layer

The Persistence Layer wraps PostgreSQL access behind SQLModel repository-style classes (e.g., `ServiceRepository`, `CheckResultRepository`, `AlertLogRepository`). No other layer issues raw SQL or touches a database session directly. This isolation:

- Makes it possible to swap or mock the database in tests.
- Centralizes transaction handling and retry-on-disconnect logic (NFR-005).
- Keeps schema knowledge in one place, supporting NFR-013 (adding new protocols without breaking existing queries).

### 4.3 Analytics Engine

The Analytics Engine reads from `CheckResultRepository` and computes derived statistics on demand (uptime percentage, average response time, failure frequency) rather than maintaining continuously updated aggregate tables in version 1.0. This keeps the write path (the Monitoring Engine) simple and fast, at the cost of computing aggregates at query time. Given the single-user, single-machine scale target (NFR-001: ~50 services), this trade-off is acceptable; a future version could introduce materialized aggregate tables if query volume grows.

The Analytics Engine also implements pattern detection (FR-024) by grouping incidents by hour-of-day and day-of-week and flagging statistically unusual clustering.

### 4.4 Alerting Engine

The Alerting Engine subscribes to status-change events from the Monitoring Engine. For each event it:

1. Checks whether the service's configured consecutive-failure threshold has been crossed (FR-031).
2. Determines which channels are enabled for that service (FR-032).
3. Dispatches through the corresponding channel adapter (`DesktopNotifier`, `EmailNotifier`, `DiscordWebhookNotifier`).
4. Writes an entry to the alert log via `AlertLogRepository` (FR-033).

Each channel adapter is isolated behind a common `NotificationChannel` interface, so a channel failing (e.g., SMTP server unreachable) is caught and logged without preventing the other channels from firing.

### 4.5 Desktop Application (UI)

Built with PySide6, the UI is organized into:

- **Dashboard View**: subscribes to live status events and renders color-coded service cards (FR-035, FR-036).
- **Service Detail View**: requests historical data and charts from the Service Layer, rendered with PyQtGraph (FR-037).
- **Service Management Dialogs**: add/edit/delete forms that call into the Service Layer, which validates input before touching the Persistence Layer (FR-038, FR-039, FR-040).
- **Settings Screens**: global settings, SMTP configuration, Discord webhook configuration, database configuration (FR-043 to FR-047).

The UI never calls the Monitoring Engine, Analytics Engine, or Persistence Layer directly. It only calls the Service Layer, which keeps the dependency graph one-directional and makes it possible to replace PySide6 with another toolkit later without touching business logic.

## 5. Data Flow

### 5.1 Monitoring Check Flow

```
Scheduler tick
   → Checker.run(service_config)
   → CheckResult produced
   → CheckResultRepository.save(result)
   → publish("service_status_changed", result)
        ├─→ Alerting Engine evaluates thresholds
        └─→ Dashboard live status widget updates
```

### 5.2 Analytics Request Flow

```
User opens Service Detail View
   → UI calls ServiceLayer.get_analytics(service_id, date_range)
   → ServiceLayer calls AnalyticsEngine.compute(service_id, date_range)
   → AnalyticsEngine queries CheckResultRepository
   → Aggregated stats returned to UI
   → PyQtGraph renders trend chart
```

### 5.3 Alert Dispatch Flow

```
service_status_changed event (status = DOWN, consecutive_failures >= threshold)
   → AlertingEngine.evaluate(event)
   → for each enabled channel:
        NotificationChannel.send(service, event)
   → AlertLogRepository.save(alert_record)
```

## 6. Database Design

The schema is normalized around four core tables. This is a logical design; exact column types will be finalized during implementation with SQLModel.

| Table | Purpose | Key Columns |
|---|---|---|
| `service` | Stores monitored service configuration | id, name, type (http/https/tcp/dns), target, interval_seconds, timeout_seconds, retry_count, is_paused |
| `check_result` | Stores every individual check outcome | id, service_id (FK), timestamp, success, response_time_ms, status_code, failure_reason |
| `alert_config` | Stores per-service alert settings | id, service_id (FK), threshold, desktop_enabled, email_enabled, discord_enabled |
| `alert_log` | Stores a record of every alert dispatched | id, service_id (FK), channel, timestamp, message |

`check_result` is expected to be the highest-volume table and is indexed on `(service_id, timestamp)` to support both the dashboard's "most recent check" lookup and the analytics engine's date-range queries efficiently, satisfying NFR-002 and NFR-003.

## 7. Concurrency and Async Model

LastPing runs a single asyncio event loop shared by APScheduler's `AsyncIOScheduler` and the network checkers. PySide6 runs its own Qt event loop on the main thread; the two loops are bridged using `qasync`, which integrates an asyncio loop with Qt's event loop so that async monitoring tasks and UI event handling coexist in the same process without manual thread synchronization.

Database writes from the Monitoring Engine happen on background tasks; the UI never performs a blocking database call on the Qt main thread. This prevents monitoring activity from freezing the interface, which is critical given the target of 50 concurrent services (NFR-001).

## 8. Technology Stack Justification

| Technology | Role | Why |
|---|---|---|
| Python | Primary language | Strong async support, mature ecosystem for networking and data work, aligns with the project's data/backend engineering learning goals |
| PySide6 (Qt) | Desktop UI | Native-feeling cross-platform desktop UI with mature charting integration |
| PostgreSQL | Persistence | Robust relational database well suited to normalized, time-series-like check data and analytical queries |
| SQLModel | ORM | Combines SQLAlchemy's query power with Pydantic-style typed models, reducing boilerplate while keeping type safety |
| APScheduler | Scheduling | Purpose-built for per-job interval scheduling, integrates with asyncio |
| PyQtGraph / Matplotlib | Charting | PyQtGraph for responsive, embedded real-time charts; Matplotlib as a fallback for static export-quality charts |
| qasync | Event loop bridge | Allows asyncio-based monitoring code to run alongside the Qt event loop without manual threading |

## 9. Module and Package Structure

```
lastping/
├── engine/
│   ├── scheduler.py        # APScheduler wiring, job lifecycle
│   ├── checkers/
│   │   ├── http_checker.py
│   │   ├── tcp_checker.py
│   │   └── dns_checker.py
│   └── retry.py
├── analytics/
│   ├── aggregates.py        # uptime %, avg response time, failure frequency
│   └── patterns.py          # recurring failure pattern detection
├── alerting/
│   ├── evaluator.py         # threshold evaluation
│   └── channels/
│       ├── desktop.py
│       ├── email.py
│       └── discord.py
├── persistence/
│   ├── models.py            # SQLModel table definitions
│   └── repositories.py      # ServiceRepository, CheckResultRepository, AlertLogRepository, AlertConfigRepository
├── service_layer/
│   └── application_service.py  # single entry point the UI calls into
├── ui/
│   ├── dashboard.py
│   ├── service_detail.py
│   ├── service_dialogs.py
│   └── settings.py
└── main.py
```

This structure keeps each SRS capability area (Section 8 of the SRS) mapped to a corresponding package, which supports both traceability and future extensibility (e.g., an `icmp_checker.py` can be added to `engine/checkers/` without touching any other package).

## 10. Design Patterns Used

- **Strategy Pattern**: each protocol checker (`HttpChecker`, `TcpChecker`, `DnsChecker`) implements a common interface, allowing the Scheduler to invoke any checker polymorphically.
- **Repository Pattern**: all database access is mediated through repository classes, decoupling business logic from SQLModel/SQLAlchemy details.
- **Observer / Publish-Subscribe**: the `service_status_changed` event lets the Alerting Engine and UI react to monitoring results without the Monitoring Engine knowing who is listening.
- **Facade Pattern**: the Service Layer exposes a single, simplified interface (`ApplicationService`) that the UI depends on, hiding the coordination between the Monitoring, Analytics, and Alerting engines.
- **Adapter Pattern**: each notification channel (`DesktopNotifier`, `EmailNotifier`, `DiscordWebhookNotifier`) adapts a different external API to the common `NotificationChannel` interface.

## 11. Error Handling and Resilience

- A failed check is caught at the checker level and converted into a `CheckResult` with `success=False` and a `failure_reason`; it never propagates as an unhandled exception into the scheduler.
- Database write failures are retried a bounded number of times with backoff; if persistence ultimately fails, the result is logged locally rather than silently dropped (supporting NFR-005 and NFR-006).
- A failure in one notification channel (e.g., SMTP timeout) is caught and logged independently, so it does not prevent other configured channels from firing.
- All unhandled exceptions in background tasks are logged with full context (service id, task name, timestamp) rather than allowed to terminate the event loop silently.

## 12. Logging and Observability Architecture

LastPing uses Python's standard `logging` module configured with a rotating file handler. Log verbosity and file location are user-configurable (FR-047). Log categories include:

- `engine` — scheduler activity, check start/end, retries
- `persistence` — database connection issues, query performance warnings
- `alerting` — dispatch attempts, channel failures
- `ui` — unexpected UI-level exceptions

This satisfies NFR-015 by giving the application a way to explain its own behavior, separate from the behavior of the services it monitors.

## 13. Security Architecture

Given the single-user, single-machine scope (SC-007), the security architecture is intentionally minimal but still addresses the sensitive data LastPing handles:

- SMTP credentials and the Discord webhook URL are stored in a local configuration store, not in application logs (NFR-011).
- Database credentials are read from a local configuration file or environment variables rather than hardcoded, keeping them out of version control (NFR-012).
- No inbound network listener is exposed by the application; all network activity is outbound (performing checks, sending alerts), which limits the application's own attack surface.

## 14. Deployment Architecture

Version 1.0 targets a single-machine deployment:

```
┌─────────────────────────────────────────────┐
│              User's Machine                 │
│                                             │
│  ┌────────────────┐     ┌─────────────────┐ │
│  │ LastPing       │───▶│  PostgreSQL     │ │
│  │ (Python process)│    │ (local instance)│ │
│  └───────┬────────┘     └─────────────────┘ │
│          │ outbound only                    │
└──────────┼──────────────────────────────────┘
           │
           ▼
  Monitored services (HTTP/TCP/DNS)
  Email provider (SMTP)
  Discord webhook endpoint
```

The Version 1.0 roadmap milestone includes producing an installer, which will bundle the Python runtime and dependencies so the end user does not need a separate Python environment.

## 15. Future Architecture Considerations

The following are not part of version 1.0 but are anticipated by this architecture's boundaries, consistent with the Project Vision's Long-Term Vision section:

- **SSL certificate monitoring** and **ICMP monitoring** can be added as new `Checker` implementations without modifying the Scheduler or Persistence Layer.
- **Distributed monitoring agents** would require introducing a network protocol between a central instance and remote agents; the current Service Layer facade is a natural seam where this could be introduced later.
- **Cloud synchronization** would require a sync layer above the Persistence Layer; the repository abstraction already isolates the rest of the system from this change.
- **AI-assisted anomaly detection** would extend the Analytics Engine with a new analysis module, consuming the same `CheckResultRepository` data the existing pattern-detection logic uses today.

---

*This document should be revisited as implementation progresses. Any deviation between this architecture and the actual implementation should be reflected back into this document so it remains an accurate reference.*
# Roadmap

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This roadmap sequences LastPing's development from initial setup through Version 1.0, and outlines the long-term vision beyond it. It expands on the version milestones originally sketched in the project README into concrete deliverables, exit criteria, and links back to the SRS requirements and Architecture components each milestone touches.

## 2. Versioning Philosophy

Each version is scoped so that, at the end of it, the application is runnable and demonstrably further along than the version before, rather than accumulating half-finished features across layers. Versions build bottom-up through the architecture: data layer and core engine first, then analytics and UI, then alerting, then polish and packaging.

## 3. Version 0.1 — Foundation

**Goal:** A running application skeleton with persistence and a basic window, but no monitoring yet.

- Project setup: repository structure, virtual environment, dependency management, linting/formatting configuration.
- Database schema: `service`, `check_result`, `alert_config`, `alert_log` tables created via SQLModel.
- Desktop window: a minimal PySide6 window that opens and connects to the database on launch.
- Add/Delete monitored services: basic CRUD for service configuration, with no monitoring behavior yet.

**Requirements touched:** FR-038, FR-040, SC-001–SC-004.
**Exit criteria:** A user can add and delete a service configuration and see it persist across an application restart.

## 4. Version 0.2 — Core Monitoring

**Goal:** Services are actually monitored and results are recorded.

- HTTP monitoring: implement `HttpChecker` and wire it into a scheduler.
- Background scheduler: integrate APScheduler with the asyncio event loop, bridged to Qt via `qasync`.
- Save monitoring history: persist every check result to `check_result`.

**Requirements touched:** FR-001, FR-005–FR-010, FR-011–FR-017.
**Exit criteria:** An HTTP service configured in the app is checked automatically on its configured interval, and every check result is stored and queryable from the database.

## 5. Version 0.3 — Analytics and Visibility

**Goal:** The historical data collected in 0.2 becomes visible and useful.

- Dashboard: status cards for all configured services with live status updates.
- Historical metrics: uptime percentage, average response time, failure frequency calculations.
- Charts: response time trend chart using PyQtGraph, with 24h/7d/30d/custom range filtering.

**Requirements touched:** FR-019–FR-025, FR-035–FR-037.
**Exit criteria:** A user can open a service's detail view and see an accurate uptime percentage and a rendered response-time trend chart for at least a 30-day range.

## 6. Version 0.4 — Alerting

**Goal:** The application proactively tells the user when something is wrong.

- Alerting system: threshold evaluation on consecutive failures.
- Notifications: desktop notification channel implemented first, followed by email and Discord channel adapters.
- Error logging: application-level logging (scheduler, persistence, alerting) per the Observability design principle.

**Requirements touched:** FR-027–FR-034, NFR-015.
**Exit criteria:** A simulated service outage triggers a desktop notification, and, once configured, an email and/or Discord alert, with the event recorded in the Alerts Log.

## 7. Version 1.0 — Completion and Packaging

**Goal:** A complete, portfolio-ready desktop monitoring application.

- Complete desktop monitoring application: TCP and DNS checkers added (extending the protocol coverage started with HTTP in 0.2), all four checkers exercised through the Add Service dialog.
- Analytics dashboard: incident history grouping (FR-023), recurring pattern detection (FR-024), and the Incidents cross-service view from the UI/UX Design document.
- Export reports: CSV export of analytics (FR-026) and configuration export/import (FR-049, FR-050).
- Installer: a packaged, single-file build via PyInstaller, per the Deployment Guide.

**Requirements touched:** FR-002–FR-004, FR-026, FR-041–FR-050, all remaining NFRs.
**Exit criteria:** All Version 1.0 functional requirements in the SRS pass their corresponding test cases in the Test Plan, and the application can be installed and run from a packaged build without a manual Python environment.

## 8. Milestone Summary Table

| Version | Focus | Key SRS Requirements | Key Architecture Components |
|---|---|---|---|
| 0.1 | Foundation | FR-038, FR-040 | Persistence Layer, minimal UI shell |
| 0.2 | Core Monitoring | FR-001, FR-005–FR-017 | Monitoring Engine, Scheduler |
| 0.3 | Analytics & Visibility | FR-019–FR-025, FR-035–FR-037 | Analytics Engine, Dashboard, Service Detail |
| 0.4 | Alerting | FR-027–FR-034 | Alerting Engine, Notification Channels |
| 1.0 | Completion & Packaging | FR-002–FR-004, FR-026, FR-041–FR-050 | Full stack + installer |

## 9. Post-1.0 / Long-Term Vision

These items, carried over from the Project Vision's Long-Term Vision section, are intentionally deferred past 1.0 and are not yet scoped into formal requirements:

- **SSL certificate monitoring** — a natural extension of the `Checker` interface (see Developer Guide, Section 5).
- **ICMP monitoring** — same extension pattern as SSL certificate monitoring.
- **AI-assisted anomaly detection** — an extension of the Analytics Engine's existing pattern-detection module (FR-024).
- **Predictive outage analysis / performance forecasting** — builds on top of anomaly detection once enough historical data exists.
- **Root cause recommendations** — a further analytics capability layered on incident grouping and pattern detection.
- **Distributed monitoring agents** — would require a new inter-process or network protocol between a central instance and remote agents, introduced at the Service Layer seam identified in the Architecture document.
- **Cloud synchronization** — would require a sync layer above the Persistence Layer.
- **Docker/Kubernetes integration** — new checker types plus, for Kubernetes, likely a new data model for cluster/pod-level status.

None of these are committed to a specific version yet; they will be formally scoped (new FRs, updated architecture sections) once Version 1.0 is stable and in actual use.

## 10. Rough Timeline Guidance

Given this is a solo, portfolio-oriented project worked on alongside an active job search, the roadmap is intentionally structured by scope rather than fixed calendar dates. As a general guide:

- Versions 0.1–0.2 are the highest-leverage for demonstrating backend/systems engineering skill (async scheduling, persistence design) and are worth prioritizing if time is limited before an interview.
- Version 0.3 is the highest-leverage for demonstrating data/analytics skill.
- Version 0.4 and the 1.0 packaging milestone round out the "complete product" story but can reasonably follow after 0.1–0.3 are solid, since a working monitoring-and-analytics core is already a strong standalone portfolio artifact even before alerting and packaging are finished.

---

*This roadmap should be revisited at the start of each version to confirm scope still matches the SRS, and updated if requirements shift during implementation.*
# Software Requirements Specification (SRS)

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This document is the Software Requirements Specification (SRS) for LastPing, a desktop-based uptime monitoring and service analytics application. It translates the goals described in the LastPing Project Vision document into a structured set of functional and non-functional requirements that will guide design, implementation, and testing.

LastPing is built in Python using PySide6 for the desktop interface, PostgreSQL for persistence, SQLModel as the ORM layer, and APScheduler for background job scheduling. The application monitors HTTP, HTTPS, TCP, and DNS services, stores historical results, and surfaces analytics that explain not just whether a service is up, but why it failed and how its reliability has trended over time.

This SRS is intended to be used as a working reference throughout development and as a portfolio artifact demonstrating professional requirements engineering practice.

## 2. Purpose

The purpose of this document is to:

- Define the complete set of functional and non-functional requirements for LastPing version 1.0.
- Establish a shared, unambiguous understanding of what the system will and will not do.
- Provide a basis for design, implementation, testing, and acceptance of the system.
- Serve as a traceability anchor linking business objectives to individual requirements, use cases, and test criteria.

This document is intended for the developer (acting as both product owner and engineer), and for anyone reviewing the project for technical or portfolio evaluation purposes.

## 3. Scope

LastPing v1.0 is a single-user desktop application. It will:

- Continuously monitor HTTP, HTTPS, TCP, and DNS services on configurable intervals.
- Persist every monitoring result, including timestamps, response times, status codes, and failure reasons, to a PostgreSQL database.
- Present a dashboard showing current service health and historical performance.
- Generate analytics including uptime percentage, average response time, failure frequency, and incident history.
- Notify the user of outages and recoveries through desktop notifications, email, and Discord webhooks.

The following are explicitly out of scope for version 1.0: distributed monitoring agents, cloud synchronization, multi-user/team collaboration, mobile applications, Kubernetes integration, and enterprise features such as RBAC, SSO, and high-availability clustering. These are captured in the Long-Term Vision section of the project vision document and may be revisited in future versions.

## 4. Definitions and Acronyms

| Term | Definition |
|---|---|
| SRS | Software Requirements Specification |
| ORM | Object-Relational Mapper |
| TCP | Transmission Control Protocol |
| DNS | Domain Name System |
| HTTP/HTTPS | Hypertext Transfer Protocol (Secure) |
| Uptime | Percentage of time a monitored service was reachable and healthy |
| Incident | A recorded period during which a monitored service was down or degraded |
| Latency | Time delay between a request and its response |
| Webhook | An HTTP callback used to push event data to an external service (e.g. Discord) |
| Scheduler | The background component (APScheduler) responsible for triggering checks at configured intervals |
| Degraded | A service state where checks succeed but response times or error rates exceed a defined threshold |
| FR | Functional Requirement |
| NFR | Non-Functional Requirement |
| UC | Use Case |

## 5. Product Overview

LastPing is a lightweight, single-user desktop application that treats uptime monitoring as an analytical process rather than a simple alerting mechanism. Where typical monitoring tools tell a user that a service went down, LastPing aims to help the user understand the conditions that led up to the failure by retaining and visualizing historical operational data.

The system is composed of four cooperating layers:

1. **Monitoring Engine** — an asynchronous scheduler that performs HTTP, HTTPS, TCP, and DNS checks against configured services.
2. **Persistence Layer** — a PostgreSQL database, accessed through SQLModel, that stores service configurations, individual check results, and derived incident records.
3. **Analytics Layer** — logic that aggregates raw check data into uptime percentages, response time trends, and failure statistics.
4. **Desktop Application (PySide6)** — the user-facing dashboard, service management screens, historical charts, and alert configuration.

## 6. Stakeholders

| Stakeholder | Interest in the System |
|---|---|
| Developer / Product Owner (Jose Medina) | Builds, maintains, and evaluates the project as a portfolio-quality demonstration of software and data engineering skill. |
| Software Developers (end users) | Use LastPing to monitor APIs and self-hosted services they maintain. |
| System Administrators (end users) | Use LastPing to monitor internal infrastructure and review historical reliability. |
| Students (end users) | Use LastPing to learn monitoring, networking, and systems concepts. |
| Technology Enthusiasts (end users) | Use LastPing to monitor personal projects, game servers, and home lab services. |
| Future Interviewers / Reviewers | Evaluate the project's architecture, documentation, and engineering rigor. |

## 7. User Personas

### Persona 1: Daniel — Backend Developer
Daniel maintains several self-hosted APIs for side projects. He wants to know quickly when an endpoint goes down and wants historical response-time data so he can tell whether a recent deploy introduced a regression.

### Persona 2: Priya — System Administrator
Priya manages internal infrastructure for a small company, including DNS records and internal TCP services. She wants a lightweight tool that does not require standing up an enterprise monitoring stack, and she wants an incident history she can reference during postmortems.

### Persona 3: Marcus — Computer Science Student
Marcus is learning about distributed systems and wants to understand how monitoring tools work under the hood. He uses LastPing both as a tool and as a reference implementation for his own learning.

### Persona 4: Elena — Home Lab Enthusiast
Elena self-hosts a Minecraft server, a personal website, and a handful of home automation services. She wants simple uptime tracking with Discord alerts so she is notified in the same place her friends already communicate.

## 8. Functional Requirements

Each requirement is assigned a unique ID for traceability. Requirements are grouped by capability area.

### 8.1 Monitoring (FR-001 to FR-010)

- **FR-001**: The system shall support monitoring of HTTP endpoints.
- **FR-002**: The system shall support monitoring of HTTPS endpoints, including SSL/TLS handshake validation.
- **FR-003**: The system shall support monitoring of arbitrary TCP ports on a configured host.
- **FR-004**: The system shall support DNS lookup monitoring to confirm a domain resolves within an expected time.
- **FR-005**: The system shall allow the user to configure a monitoring interval per service, independent of other services.
- **FR-006**: The system shall allow the user to configure a request timeout per service.
- **FR-007**: The system shall retry a failed check a configurable number of times before marking the service as down.
- **FR-008**: The system shall allow the user to manually trigger an immediate check for a given service outside its normal schedule.
- **FR-009**: The system shall allow the user to pause and resume monitoring for an individual service without deleting its configuration.
- **FR-010**: The system shall be capable of monitoring multiple services concurrently using asynchronous execution, without one slow check blocking others.

### 8.2 Data Collection (FR-011 to FR-018)

- **FR-011**: The system shall record a timestamp for every monitoring check performed.
- **FR-012**: The system shall record the response time for every check that completes.
- **FR-013**: The system shall record the HTTP status code returned for HTTP/HTTPS checks.
- **FR-014**: The system shall record a human-readable failure reason when a check fails (e.g., timeout, connection refused, DNS resolution failure).
- **FR-015**: The system shall record a success/failure result for every check.
- **FR-016**: The system shall record latency values for TCP and DNS checks.
- **FR-017**: The system shall persist all check results to the PostgreSQL database without data loss under normal operating conditions.
- **FR-018**: The system shall retain historical monitoring data indefinitely unless the user explicitly purges it.

### 8.3 Analytics (FR-019 to FR-026)

- **FR-019**: The system shall calculate uptime percentage for a service over a user-selectable time range.
- **FR-020**: The system shall calculate average response time for a service over a user-selectable time range.
- **FR-021**: The system shall calculate failure frequency for a service over a user-selectable time range.
- **FR-022**: The system shall display a historical response time trend chart for each monitored service.
- **FR-023**: The system shall display a chronological incident history for each monitored service.
- **FR-024**: The system shall identify and surface recurring failure patterns (e.g., failures clustering at a similar time of day).
- **FR-025**: The system shall allow the user to filter analytics and charts by a custom date range.
- **FR-026**: The system shall allow the user to export analytics data for a service to CSV.

### 8.4 Alerts and Notifications (FR-027 to FR-034)

- **FR-027**: The system shall send a desktop notification when a monitored service transitions to a down state.
- **FR-028**: The system shall send a desktop notification when a monitored service recovers from a down state.
- **FR-029**: The system shall send an email alert when a monitored service goes down, if email alerting is configured.
- **FR-030**: The system shall send a Discord webhook message when a monitored service goes down, if Discord alerting is configured.
- **FR-031**: The system shall allow the user to configure an alert threshold (e.g., number of consecutive failed checks) before an alert is triggered.
- **FR-032**: The system shall allow the user to configure which notification channels (desktop, email, Discord) are active per service.
- **FR-033**: The system shall maintain a log of all alerts sent, including timestamp, channel, and associated service.
- **FR-034**: The system shall allow the user to temporarily snooze or mute alerts for a specific service.

### 8.5 Dashboard and User Interface (FR-035 to FR-042)

- **FR-035**: The system shall display a dashboard showing the current status of all monitored services.
- **FR-036**: The system shall use color-coded indicators to represent service state (e.g., healthy, degraded, down).
- **FR-037**: The system shall provide a service detail view showing historical charts and recent check results for a single service.
- **FR-038**: The system shall allow the user to add a new monitored service through the desktop interface.
- **FR-039**: The system shall allow the user to edit the configuration of an existing monitored service.
- **FR-040**: The system shall allow the user to delete a monitored service, with confirmation, including its historical data or an option to retain it.
- **FR-041**: The system shall allow the user to search and filter the list of monitored services by name, type, or status.
- **FR-042**: The system shall support a dark mode display theme (planned for a later 1.x release).

### 8.6 Configuration and Settings (FR-043 to FR-047)

- **FR-043**: The system shall provide a global settings screen for default monitoring interval and default retry count.
- **FR-044**: The system shall provide a configuration screen for SMTP settings used for email alerts.
- **FR-045**: The system shall provide a configuration screen for the Discord webhook URL used for alerts.
- **FR-046**: The system shall provide configuration for the PostgreSQL database connection used by the application.
- **FR-047**: The system shall provide configuration for application logging verbosity and log file location.

### 8.7 Data Management (FR-048 to FR-050)

- **FR-048**: The system shall allow the user to purge historical monitoring data older than a configurable number of days.
- **FR-049**: The system shall allow the user to export service configurations as a backup file.
- **FR-050**: The system shall allow the user to import a previously exported service configuration file.

## 9. Non-Functional Requirements

### 9.1 Performance

- **NFR-001**: The system shall be able to monitor at least 50 concurrent services without degradation of check accuracy or UI responsiveness.
- **NFR-002**: The dashboard shall reflect a status change within 2 seconds of a check completing.
- **NFR-003**: Historical chart queries covering up to 90 days of data shall render within 3 seconds under normal load.

### 9.2 Reliability

- **NFR-004**: The monitoring engine shall continue operating and logging results even if one or more monitored services are unreachable.
- **NFR-005**: The application shall recover from a database connection interruption without losing in-flight check results, where feasible, by buffering and retrying writes.
- **NFR-006**: The system shall not crash as a result of a single failed or malformed check.

### 9.3 Usability

- **NFR-007**: A new user shall be able to add their first monitored service without consulting external documentation.
- **NFR-008**: Status indicators and terminology shall be consistent across the dashboard, detail views, and alerts.

### 9.4 Maintainability

- **NFR-009**: The codebase shall be organized into clearly separated modules for monitoring, persistence, analytics, alerting, and UI.
- **NFR-010**: Core business logic (monitoring, analytics, alerting) shall be decoupled from the PySide6 UI layer to support independent testing.

### 9.5 Security

- **NFR-011**: Credentials such as SMTP passwords and Discord webhook URLs shall not be stored in plaintext in application logs.
- **NFR-012**: Database connection credentials shall be stored using a configuration mechanism that keeps them out of version control.

### 9.6 Scalability

- **NFR-013**: The database schema shall be normalized in a way that supports adding new monitoring protocols (e.g., ICMP, SSL certificate checks) without breaking existing queries.

### 9.7 Portability

- **NFR-014**: The application shall run on Windows and Linux desktop environments supported by PySide6 and Python 3.

### 9.8 Observability

- **NFR-015**: The application shall log its own operational events (scheduler activity, database errors, alert dispatch failures) to a log file to support self-diagnosis.

## 10. System Constraints

- **SC-001**: The system must be implemented in Python.
- **SC-002**: The desktop interface must be built using PySide6 (Qt for Python).
- **SC-003**: Persistent storage must use PostgreSQL, accessed through SQLModel.
- **SC-004**: Background scheduling must use APScheduler.
- **SC-005**: Charting must use PyQtGraph or Matplotlib.
- **SC-006**: Version control must be managed through Git and hosted on GitHub.
- **SC-007**: Version 1.0 is a single-user, single-machine application; no server-side or multi-device component is in scope.

## 11. Assumptions and Dependencies

- It is assumed the user has a PostgreSQL instance available, either local or reachable over the network.
- It is assumed the user has network access to the services they configure for monitoring.
- Email alerting depends on the user having access to a working SMTP account.
- Discord alerting depends on the user having permission to create a webhook in a Discord server they control.
- It is assumed the application will primarily be used by a single technically proficient user rather than a non-technical audience.
- It is assumed the initial deployment target is a developer's own machine rather than a packaged installer, until the Version 1.0 installer milestone is reached.

## 12. User Stories

- **US-001**: As a developer, I want to add an HTTP endpoint to be monitored so that I am notified if my API goes down.
- **US-002**: As a system administrator, I want to monitor internal TCP services so that I know immediately if a critical internal system becomes unreachable.
- **US-003**: As a system administrator, I want to review an incident history so that I can reference past outages during a postmortem.
- **US-004**: As a developer, I want to see a response time trend chart so that I can tell if a recent deployment slowed down my service.
- **US-005**: As a home lab enthusiast, I want Discord alerts so that I am notified in the same app I already use with friends.
- **US-006**: As a student, I want to explore how uptime percentage is calculated so that I can learn how monitoring systems work.
- **US-007**: As any user, I want to pause monitoring for a service temporarily so that planned maintenance does not trigger false alerts.
- **US-008**: As any user, I want to export my service configuration so that I can back it up or move it to another machine.
- **US-009**: As any user, I want to set an alert threshold so that a single transient blip does not trigger an unnecessary notification.
- **US-010**: As any user, I want a dashboard overview so that I can see the health of all my services at a glance.

## 13. Use Cases

### UC-01: Add a Monitored Service
**Actor:** User
**Preconditions:** The application is running and connected to the database.
**Main Flow:**
1. User selects "Add Service" from the dashboard.
2. User selects a monitoring type (HTTP, HTTPS, TCP, or DNS).
3. User enters the target address, check interval, timeout, and retry count.
4. User optionally configures alert channels for this service.
5. User saves the configuration.
6. The system validates the configuration and begins scheduling checks.
**Postconditions:** The new service appears on the dashboard and is actively monitored.
**Related Requirements:** FR-001 through FR-006, FR-038.

### UC-02: View Service Dashboard
**Actor:** User
**Preconditions:** At least one service is configured.
**Main Flow:**
1. User opens the application.
2. The dashboard loads and displays all configured services with current status indicators.
3. User can filter or search the service list.
**Postconditions:** User has an up-to-date view of overall system health.
**Related Requirements:** FR-035, FR-036, FR-041.

### UC-03: Investigate a Service Incident
**Actor:** User
**Preconditions:** A service has experienced at least one failure.
**Main Flow:**
1. User opens the detail view for a service.
2. User reviews the historical response time chart and incident list.
3. User filters the view by a custom date range to isolate the incident window.
4. User identifies a recurring pattern or root cause using the displayed analytics.
**Postconditions:** User has sufficient historical context to explain the failure.
**Related Requirements:** FR-019 through FR-025, FR-037.

### UC-04: Configure and Receive an Alert
**Actor:** User
**Preconditions:** A service is configured with at least one alert channel enabled.
**Main Flow:**
1. User configures an alert threshold and selects notification channels (desktop, email, Discord).
2. The monitored service fails enough consecutive checks to cross the configured threshold.
3. The system sends notifications through all configured channels.
4. The system logs the alert event.
**Postconditions:** The user is informed of the outage through their chosen channels, and the alert is recorded.
**Related Requirements:** FR-027 through FR-034.

### UC-05: Purge Historical Data
**Actor:** User
**Preconditions:** Historical monitoring data older than the retention window exists.
**Main Flow:**
1. User navigates to data management settings.
2. User specifies a retention period.
3. User confirms the purge action.
4. The system deletes monitoring records older than the specified period.
**Postconditions:** Database size is reduced while recent history is preserved.
**Related Requirements:** FR-048.

## 14. Acceptance Criteria

**AC for FR-001/FR-002 (HTTP/HTTPS Monitoring)**
- Given a valid HTTP or HTTPS URL is configured, when the scheduled check runs, then the system records a status code, response time, and success/failure result.

**AC for FR-007 (Retry Handling)**
- Given a service configured with a retry count of N, when a check fails, then the system retries up to N times before marking the service as down, and only the final result is recorded as the check outcome.

**AC for FR-017/FR-018 (Persistence and Retention)**
- Given the application is monitoring a service, when a check completes, then the result is committed to PostgreSQL such that it survives an application restart.

**AC for FR-019 (Uptime Percentage)**
- Given a service with a mix of successful and failed checks over a selected time range, when the user views the analytics for that service, then the displayed uptime percentage equals (successful checks ÷ total checks) × 100 for that range.

**AC for FR-027/FR-031 (Alert Threshold)**
- Given an alert threshold of N consecutive failures, when fewer than N consecutive failures have occurred, then no alert is sent; when N consecutive failures have occurred, then an alert is sent exactly once until the service recovers.

**AC for FR-040 (Service Deletion)**
- Given a user selects delete on a monitored service, when the user confirms the action, then the service is removed from active monitoring and the user is given an explicit choice of whether to also delete its historical data.

**AC for FR-048 (Data Purge)**
- Given a configured retention period of X days, when the user initiates a purge, then all check records with a timestamp older than X days are removed and records within the window remain untouched.

## 15. Requirement Traceability Matrix

| Requirement ID | Objective (from Project Vision) | Related Use Case(s) | Related User Story | Acceptance Criteria |
|---|---|---|---|---|
| FR-001, FR-002 | Monitor HTTP and HTTPS endpoints | UC-01 | US-001 | AC for FR-001/FR-002 |
| FR-003 | Monitor TCP services | UC-01 | US-002 | AC for FR-007 (retry applies) |
| FR-004 | Monitor DNS availability | UC-01 | US-006 | AC for FR-001/FR-002 (pattern applies) |
| FR-005, FR-006 | Execute health checks on configurable intervals | UC-01 | US-001 | AC for FR-001/FR-002 |
| FR-007 | Reliability of monitoring engine | UC-01 | US-009 | AC for FR-007 |
| FR-008, FR-009 | Simplicity and operational control | UC-01 | US-007 | — |
| FR-010 | Scalability of monitoring engine | UC-02 | US-010 | — |
| FR-011–FR-016 | Store monitoring results persistently | UC-01, UC-03 | US-004 | AC for FR-017/FR-018 |
| FR-017, FR-018 | Maintain historical monitoring data | UC-03 | US-003 | AC for FR-017/FR-018 |
| FR-019–FR-021 | Generate operational analytics | UC-03 | US-004, US-006 | AC for FR-019 |
| FR-022–FR-025 | Historical charts, incident history | UC-03 | US-003, US-004 | AC for FR-019 |
| FR-026 | Actionable operational insights | UC-03 | US-008 (export pattern) | — |
| FR-027–FR-030 | Notify users of service interruptions | UC-04 | US-005 | AC for FR-027/FR-031 |
| FR-031–FR-034 | Alert configurability and history | UC-04 | US-009 | AC for FR-027/FR-031 |
| FR-035–FR-037 | Display current service health | UC-02, UC-03 | US-010 | — |
| FR-038–FR-041 | Service management | UC-01 | US-001, US-007 | AC for FR-040 |
| FR-042 | Desktop interface polish | — | — | — |
| FR-043–FR-047 | Configuration settings | UC-01, UC-04 | US-005, US-009 | — |
| FR-048 | Data lifecycle management | UC-05 | US-008 (adjacent) | AC for FR-048 |
| FR-049, FR-050 | Portability of configuration | — | US-008 | — |
| NFR-001–NFR-003 | Scalability, Reliability design principles | UC-02 | US-010 | — |
| NFR-004–NFR-006 | Reliability design principle | UC-01, UC-04 | US-002 | — |
| NFR-009, NFR-010 | Maintainability design principle | — | — | — |
| NFR-011, NFR-012 | Secure handling of credentials | UC-04 | US-005 | — |
| NFR-013 | Extensibility design principle | — | — | — |
| NFR-015 | Observability design principle | — | — | — |

---

*This SRS should be treated as a living document. As LastPing progresses through its version roadmap (0.1 through 1.0), requirements may be refined, and new requirements may be added as scope evolves, particularly around the long-term vision items such as SSL certificate monitoring and anomaly detection.*
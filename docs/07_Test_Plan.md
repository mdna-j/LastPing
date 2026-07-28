# Test Plan

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This Test Plan defines how LastPing will be verified against the functional and non-functional requirements in the SRS. It covers test strategy, scope, environment, tooling, and requirement-to-test-case traceability, and is intended to be executed incrementally as each roadmap version (0.1–1.0) is built rather than only at the end of the project.

## 2. Objectives

- Verify that each functional requirement (FR-001 through FR-050) behaves as specified.
- Verify that non-functional requirements (performance, reliability, maintainability) are met under realistic conditions.
- Catch regressions early by running the automated test suite as part of routine development, not only before a release.
- Provide evidence, in the form of passing tests, that supports the "Technical Success" and "Portfolio Success" criteria defined in the Project Vision.

## 3. Scope

### In Scope

- Unit testing of the Monitoring Engine, Analytics Engine, Alerting Engine, and Persistence Layer in isolation.
- Integration testing across layers (e.g., a scheduled check resulting in a persisted row and a dispatched alert).
- UI testing of PySide6 dialogs and views where practical, focusing on validation logic and state rendering rather than pixel-level appearance.
- Performance testing against the concurrency and response-time targets in NFR-001 through NFR-003.
- Manual exploratory testing of the full add-service-to-alert-received flow (UC-01 through UC-04).

### Out of Scope

- Load testing beyond the ~50-service target defined in NFR-001, since that exceeds the intended single-user scale.
- Cross-platform automated UI testing on every OS/Qt combination; manual verification on Windows and Linux is sufficient given SC-007 and NFR-014.
- Security penetration testing, given the single-user, single-machine, outbound-only network posture described in the Architecture document.

## 4. Test Strategy

LastPing follows a layered testing approach that mirrors the layered architecture:

| Layer | Test Type | Primary Tooling |
|---|---|---|
| Checkers (HTTP/TCP/DNS) | Unit tests with mocked network calls | `pytest`, `pytest-asyncio`, `responses`/`aioresponses` |
| Persistence Layer | Integration tests against a real (test) PostgreSQL instance | `pytest`, a disposable test database, SQLModel test fixtures |
| Analytics Engine | Unit tests with seeded, known check-result datasets | `pytest` |
| Alerting Engine | Unit tests with mocked notification channels (fake SMTP server, mocked Discord webhook endpoint) | `pytest`, `unittest.mock` |
| Service Layer | Integration tests exercising full use cases end-to-end against a test database | `pytest` |
| UI (PySide6) | Widget-level tests for validation and state rendering | `pytest-qt` |
| Performance | Scripted load generation against N simulated services | Custom `pytest` benchmarks / `pytest-benchmark` |

Unit tests for the Monitoring, Analytics, and Alerting engines require no PySide6 or database dependency to run, which is a direct payoff of the layered architecture's separation of concerns (NFR-009, NFR-010).

## 5. Test Environment

- **Local development**: Python virtual environment, a local PostgreSQL instance dedicated to testing (separate from any development data), run via `pytest`.
- **Continuous testing**: tests run locally before each commit/push during solo development; a GitHub Actions workflow can be added later to run the suite automatically on push, using a PostgreSQL service container.
- **Test data**: fixtures generate synthetic `service` and `check_result` rows covering healthy, degraded, and down states across multiple time ranges, so analytics calculations can be verified against known expected values.

## 6. Test Case Traceability

| Test Case ID | Requirement(s) Covered | Description | Type |
|---|---|---|---|
| TC-001 | FR-001, FR-002 | HTTP/HTTPS checker returns correct status code and response time for a mocked successful response | Unit |
| TC-002 | FR-001, FR-002, FR-014 | HTTP/HTTPS checker records a correct failure reason for a timeout | Unit |
| TC-003 | FR-003 | TCP checker correctly detects an open vs. closed port | Unit |
| TC-004 | FR-004 | DNS checker correctly detects a resolvable vs. unresolvable domain | Unit |
| TC-005 | FR-005, FR-006 | Scheduler respects per-service interval and timeout configuration | Integration |
| TC-006 | FR-007 | Retry handler retries the configured number of times before finalizing a failure | Unit |
| TC-007 | FR-008 | Manual "check now" triggers an out-of-band check without disrupting the schedule | Integration |
| TC-008 | FR-009 | Pausing a service stops its scheduled checks; resuming restarts them | Integration |
| TC-009 | FR-010 | Multiple services check concurrently without one slow check delaying another | Integration/Performance |
| TC-010 | FR-011–FR-017 | A completed check results in a correctly populated `check_result` row | Integration |
| TC-011 | FR-018, FR-048 | Data purge removes records older than the configured retention period and preserves newer records | Integration |
| TC-012 | FR-019 | Uptime percentage calculation matches (successes ÷ total) × 100 against a seeded dataset | Unit |
| TC-013 | FR-020, FR-021 | Average response time and failure frequency calculations match expected values against a seeded dataset | Unit |
| TC-014 | FR-022, FR-025 | Trend chart data is correctly filtered by a custom date range | Unit/Integration |
| TC-015 | FR-023 | Incident history correctly groups consecutive failed checks into a single incident with start/end time | Unit |
| TC-016 | FR-024 | Recurring failure pattern detection flags a synthetic dataset with clustered failures at a consistent hour | Unit |
| TC-017 | FR-026 | CSV export produces a file matching the on-screen analytics values | Integration |
| TC-018 | FR-027, FR-028 | Desktop notification fires on down transition and again on recovery | Integration (mocked OS notifier) |
| TC-019 | FR-029 | Email alert is sent via a mocked SMTP server on failure threshold breach | Integration |
| TC-020 | FR-030 | Discord webhook alert is sent via a mocked HTTP endpoint on failure threshold breach | Integration |
| TC-021 | FR-031 | No alert fires below threshold; exactly one alert fires once threshold is crossed | Unit |
| TC-022 | FR-032 | Only the channels enabled for a given service are invoked | Unit |
| TC-023 | FR-033 | Every dispatched alert is written to the alert log with correct metadata | Integration |
| TC-024 | FR-034 | A muted/snoozed service does not dispatch alerts during the snooze window | Unit |
| TC-025 | FR-035, FR-036 | Dashboard renders correct status color/icon for each of the four service states | UI |
| TC-026 | FR-038, FR-039, FR-040 | Add/Edit/Delete service dialogs validate input and correctly update the database | UI/Integration |
| TC-027 | FR-041 | Search/filter narrows the service list correctly by name, type, and status | UI |
| TC-028 | FR-049, FR-050 | Exported configuration can be re-imported and results in an identical service set | Integration |
| TC-029 | NFR-001 | System sustains 50 concurrently monitored services without missed check intervals | Performance |
| TC-030 | NFR-002 | Dashboard status reflects a completed check within 2 seconds | Performance |
| TC-031 | NFR-003 | A 90-day trend chart query completes within 3 seconds | Performance |
| TC-032 | NFR-004, NFR-006 | A single failing/unreachable service does not affect monitoring of other services or crash the app | Integration |
| TC-033 | NFR-005 | A simulated database disconnect is recovered from without losing an in-flight check result | Integration |
| TC-034 | NFR-011, NFR-012 | Credentials are absent from log output and are not committed to version control by default configuration | Manual/Static review |

## 7. Entry and Exit Criteria

**Entry criteria** for a testing cycle on a given roadmap version:
- The relevant feature(s) for that version are implementation-complete and runnable locally.
- Test fixtures/seed data exist for any new data shape introduced.

**Exit criteria** for a testing cycle:
- All test cases mapped to that version's requirements pass.
- No open defects are classified as blocking (crashes, data loss, or a core use case from Section 13 of the SRS being unusable).
- Any known non-blocking issues are documented rather than silently deferred.

## 8. Defect Management

Given this is a solo project, defects are tracked as GitHub Issues, labeled by severity:

- **Blocking**: crashes, data loss, or a core use case (UC-01–UC-05) cannot be completed.
- **Major**: a requirement is not met but a workaround exists.
- **Minor**: cosmetic or edge-case issues that do not affect core functionality.

Blocking and major defects must be resolved before a version is considered complete per the roadmap.

## 9. Risk-Based Testing Priorities

Given limited solo-developer time, testing effort is prioritized in this order:

1. **Data integrity** (TC-010, TC-011, TC-033) — losing or corrupting historical data undermines the entire premise of the project.
2. **Monitoring correctness** (TC-001–TC-009) — incorrect checks produce false confidence or false alarms.
3. **Alerting correctness** (TC-018–TC-024) — a missed or duplicate alert is a high-visibility failure for the user.
4. **Analytics correctness** (TC-012–TC-017) — incorrect statistics undermine the project's core value proposition of explaining *why*, not just *that*, a failure occurred.
5. **UI and performance** (TC-025–TC-031) — important for usability but lower risk of silent, undetected failure than the categories above.

---

*This Test Plan should be updated as new requirements are added (e.g., SSL certificate monitoring in a future version) so that traceability between requirements and test cases remains current.*
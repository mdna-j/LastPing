# Developer Guide

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This guide is for anyone (including future-me) working on the LastPing codebase. It covers environment setup, project structure, coding conventions, and step-by-step instructions for extending the system in the ways the architecture was explicitly designed to support: new monitoring protocols, new notification channels, and new analytics.

## 2. Getting Started

```bash
git clone https://github.com/<your-username>/lastping.git
cd lastping
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest, pytest-asyncio, pytest-qt, etc.
```

Set up a local test database separate from any development database:

```bash
createdb lastping_dev
createdb lastping_test
```

Copy `config.example.toml` to `config.toml` and fill in local values (see the Deployment Guide, Section 4).

Run the test suite to confirm the environment is working:

```bash
pytest
```

## 3. Project Structure

The package layout mirrors the System Architecture document's layers:

```
lastping/
├── engine/          # Monitoring Engine: scheduler + protocol checkers
├── analytics/        # Analytics Engine: aggregates + pattern detection
├── alerting/          # Alerting Engine: threshold evaluation + channels
├── persistence/       # SQLModel models + repositories
├── service_layer/     # Facade the UI depends on
├── ui/                # PySide6 views, dialogs, widgets
└── main.py
```

**Rule of thumb**: if you're adding code and you're not sure where it goes, ask which layer in the Architecture document owns that responsibility, and put the code there. The UI package should never import directly from `engine`, `analytics`, `alerting`, or `persistence` — only from `service_layer`.

## 4. Coding Standards

- Follow PEP 8; format with `black` and lint with `ruff` (or your preferred equivalents) before committing.
- Type hints are required on all public functions and class attributes, since SQLModel and the repository pattern both rely on typed models for clarity.
- Prefer small, single-responsibility classes over large multi-purpose ones, consistent with NFR-009.
- Business logic (engine, analytics, alerting) must not import anything from `PySide6`. If a function needs to import Qt, it belongs in `ui/`, not elsewhere.
- Write docstrings for any class or function whose behavior isn't obvious from its name and signature alone.

## 5. Adding a New Monitoring Protocol (e.g., ICMP)

The architecture was designed so this is additive, not disruptive. Steps:

1. Create `engine/checkers/icmp_checker.py` implementing the shared `Checker` interface (a `run(service_config) -> CheckResult` coroutine).
2. Add `"icmp"` as a valid value in the `service.type` field/enum in `persistence/models.py`.
3. Register the new checker in the Scheduler's protocol-to-checker mapping in `engine/scheduler.py`.
4. Add the new protocol as an option in the Add/Edit Service dialog (`ui/service_dialogs.py`).
5. Write unit tests in `tests/engine/test_icmp_checker.py` following the pattern used for the existing checkers (mock the network call, assert the resulting `CheckResult` fields).
6. Update the SRS and Architecture documents to reflect the new capability once it's implemented, per the "living document" note in both.

No changes should be required to the Persistence Layer's repository classes, the Analytics Engine, or the Alerting Engine, since all of them operate on the protocol-agnostic `CheckResult` model.

## 6. Adding a New Notification Channel

1. Create a new adapter in `alerting/channels/` implementing the `NotificationChannel` interface (a `send(service, event) -> None` method).
2. Add configuration fields for the new channel to `alert_config` in `persistence/models.py` and to the Settings screen.
3. Register the channel in the Alerting Engine's channel dispatch list.
4. Write unit tests using a mocked version of the channel's external API, following the pattern used for the existing Discord/email tests.
5. Ensure a failure in the new channel is caught and logged without affecting other channels, per the Error Handling section of the Architecture document.

## 7. Database Migrations

For early versions, schema is created via `SQLModel.metadata.create_all()`. As the schema stabilizes, introduce Alembic for versioned migrations:

```bash
pip install alembic
alembic init migrations
```

Once Alembic is in place, any schema change should be accompanied by a generated migration script, committed alongside the model change in the same pull request/commit, so the schema and the models never drift apart.

## 8. Testing Guidelines

- Every new checker, analytics function, or alerting channel should ship with unit tests before being considered done, per the risk-based priorities in the Test Plan (data integrity and monitoring correctness first).
- Use the existing test fixtures for seeded `check_result` data rather than duplicating fixture logic across test files.
- Run `pytest` before every commit. If a change touches the Persistence Layer, run the integration subset against the test database (`pytest -m integration`).
- UI changes should include a `pytest-qt` test where validation logic is involved (e.g., the Add Service dialog rejecting an invalid target).

## 9. Git Workflow

- `main` should always be in a runnable state.
- Work on feature branches named by roadmap milestone or feature, e.g., `feature/tcp-checker`, `feature/dashboard-status-cards`.
- Commit messages should describe *why*, not just *what*, when the change isn't self-explanatory (e.g., "Add retry backoff to avoid false-positive alerts on transient network blips" rather than "update retry.py").
- Since this is currently a solo project, pull requests are optional, but keeping commits scoped to one logical change makes it easier to reference specific commits from interview STAR stories later.

## 10. Debugging Tips

- Set `LOG_LEVEL=DEBUG` in the local config to see detailed scheduler and checker activity.
- If a check appears "stuck," confirm the async event loop isn't being blocked by a synchronous call inside a checker (a common source of the exact problem the async architecture was designed to prevent).
- If the UI appears frozen during a database operation, verify that call is happening off the Qt main thread/event loop via the `qasync` bridge, not directly inside a Qt slot.
- For alerting issues, check the Alerts Log screen first (FR-033) before diving into logs — it will show whether an alert was attempted and what channel it targeted.

---

*Update this guide whenever a new pattern is introduced (e.g., the first time a materialized aggregate table is added to the Analytics Engine), so it stays a reliable onboarding reference.*
# Deployment Guide

**Project Name:** LastPing
**Document Version:** 1.0
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This guide describes how to install, configure, run, and package LastPing, covering both a developer setup (for building and testing) and an end-user installer path (for the Version 1.0 milestone). It assumes the reader has basic command-line familiarity but is not necessarily a Python expert, consistent with LastPing's target of also being usable by system administrators and enthusiasts (per the SRS personas).

## 2. System Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Windows 10/11 or a modern Linux desktop distribution (per NFR-014) |
| Python | 3.11 or later |
| PostgreSQL | 14 or later, local or network-accessible |
| RAM | 512 MB available to the application |
| Disk | Depends on retention settings; historical check data grows with the number of monitored services and check frequency |
| Network | Outbound network access to monitored services and, if configured, an SMTP server and/or Discord |

## 3. Installation Methods

Two installation paths are supported:

1. **Development install** — running from source in a virtual environment. Required for anyone building or modifying LastPing.
2. **Packaged installer** — a bundled executable for end users, planned as a Version 1.0 roadmap milestone, produced with PyInstaller.

### 3.1 Development Install

```bash
# Clone the repository
git clone https://github.com/<your-username>/lastping.git
cd lastping

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3.2 Database Setup

LastPing requires a PostgreSQL database to exist before first run.

```bash
# Using psql
createdb lastping

# Or inside psql
CREATE DATABASE lastping;
```

Connection details are supplied through a local configuration file or environment variables (see Section 4), never hardcoded in source, per NFR-012.

On first run, LastPing applies its schema (the four core tables described in the System Architecture document: `service`, `check_result`, `alert_config`, `alert_log`) using SQLModel's metadata creation, or a migration tool such as Alembic if migrations are introduced later in the roadmap.

## 4. Configuration

LastPing reads configuration from a local file (e.g., `config.toml` or `.env`, finalized during implementation) rather than hardcoding values, satisfying NFR-012. Configurable values include:

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `LOG_LEVEL` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE_PATH` | Location of the rotating log file |
| `DEFAULT_CHECK_INTERVAL` | Default interval applied to new services |
| `DEFAULT_RETRY_COUNT` | Default retry count applied to new services |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | Email alert configuration |
| `DISCORD_WEBHOOK_URL` | Discord alert configuration |
| `DATA_RETENTION_DAYS` | Default retention window before purge |

This configuration file should be excluded from version control (e.g., via `.gitignore`) since it may contain credentials, consistent with NFR-011 and NFR-012. A `config.example.toml` (or `.env.example`) should be committed instead, showing the expected shape without real values.

## 5. Running the Application

```bash
# From the project root, with the virtual environment active
python -m lastping.main
```

On first launch:

1. The application verifies database connectivity and applies schema if not already present.
2. The Dashboard opens with an empty service list.
3. The user adds their first monitored service (UC-01 in the SRS).

## 6. Building the Installer (Version 1.0 Milestone)

The packaged installer bundles the Python runtime, all dependencies, and the application code, so an end user does not need to install Python or manage a virtual environment.

```bash
# Install the packaging tool
pip install pyinstaller

# Build a single-file executable
pyinstaller --onefile --windowed --name LastPing lastping/main.py
```

Notes for this milestone:

- `--windowed` suppresses the console window on Windows for a native desktop feel.
- PostgreSQL itself is not bundled; the installer guide for end users should point to installing PostgreSQL separately, or, as a future enhancement, document using a bundled/embedded Postgres distribution.
- Platform-specific builds (Windows `.exe`, Linux AppImage or `.deb`) should be built and tested separately, since PyInstaller output is not cross-platform.

## 7. Upgrading

When upgrading an existing installation:

1. Back up the PostgreSQL database (`pg_dump lastping > backup.sql`) before upgrading, since schema changes may accompany new versions.
2. Pull the latest source (or install the new packaged release).
3. Apply any new database migrations (once a migration tool is introduced; for early versions using `SQLModel.metadata.create_all`, schema changes should be handled carefully and documented per release).
4. Review the release notes / roadmap document for any configuration changes required.

## 8. Troubleshooting

| Symptom | Likely Cause | Resolution |
|---|---|---|
| Application fails to start with a database connection error | PostgreSQL not running, or incorrect `DATABASE_URL` | Verify PostgreSQL is running and reachable; confirm connection string |
| No desktop notifications appear | OS notification permissions not granted, or desktop channel disabled for the service | Check OS notification settings; verify FR-032 channel configuration for the service |
| Email alerts not sending | Incorrect SMTP configuration or blocked outbound port | Verify SMTP settings in Settings screen; test with a known-working SMTP account |
| Discord alerts not sending | Invalid or expired webhook URL | Regenerate the webhook URL in Discord's channel settings and update Settings |
| Dashboard feels slow with many services | Approaching or exceeding the ~50-service target from NFR-001 | Review check intervals; consider increasing intervals for less-critical services |
| High disk usage over time | Long data retention with high check frequency | Adjust `DATA_RETENTION_DAYS` and run a manual purge (FR-048) |

## 9. Uninstallation

1. Close the application.
2. Remove the installed executable/application folder (or uninstall via the OS-standard method once a packaged installer exists).
3. Optionally drop the PostgreSQL database (`dropdb lastping`) if no historical data needs to be retained.
4. Remove the local configuration file if it contains credentials you no longer want stored on disk.

---

*This guide should be expanded once the Version 1.0 installer milestone is reached, including OS-specific screenshots and step-by-step installer walkthroughs.*
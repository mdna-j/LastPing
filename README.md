# LastPing

![CI](https://github.com/mdna-j/LastPing/actions/workflows/ci.yml/badge.svg)

LastPing is a small uptime monitoring service that stores checks and alerts when services
go down. This repository contains the API, worker loop, alerting helpers, and tests.

**Tech highlights**

- Python 3.11
- FastAPI, Uvicorn
- SQLModel / PostgreSQL
- Docker / Docker Compose for local development

**Quickstart (local)**

1. Create and activate a virtualenv (Python 3.11+ recommended):

```bash
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate     # Windows (PowerShell)
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the API locally:

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

4. Run the background worker locally:

```bash
python -m src.worker
```

**Environment variables**

- `WORKER_SCAN_INTERVAL` — seconds between worker loop iterations (default: `30`).
- `WORKER_HEALTH_FILE` — path to write a health file for orchestration (default: `/tmp/lastping_worker.health`).
- `DATABASE_URL` — SQLAlchemy/SQLModel database URL (set as appropriate for your environment).

**Docker / Docker Compose**

Use `docker build` or the provided `docker-compose.yml` to build and run containers. The worker
process can be run inside the container; ensure `DATABASE_URL` and any alerting secrets are provided
via environment or a secrets mechanism.

**Development & CI recommendations**

- Formatting: use `black` and `isort`.
- Linting & type checks: use `ruff` and `mypy` (or `ruff`'s type checks) and run them in CI.
  -- Add `pre-commit` with hooks for `black`, `isort`, and `ruff` to catch issues locally.
  -- This repository now includes a GitHub Actions workflow at `.github/workflows/ci.yml` which
  runs `ruff`, `black --check`, and the test suite on push and PR to `main`.

**Tests**

Unit tests live under `tests/`. Run `python -m pytest -q` to run them locally. The project includes
basic tests for alerts and worker behaviour; expand tests when adding features.

**Contributing**

Please open issues or PRs. Follow the existing coding style and run tests + linters before submitting.

**License**

See the `LICENSE` file at the repository root.

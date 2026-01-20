# LastPing

![CI](https://github.com/mdna-j/LastPing/actions/workflows/ci.yml/badge.svg)

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
  - Add `pre-commit` with hooks for `black`, `isort`, and `ruff` to catch issues locally.
  - This repository now includes a GitHub Actions workflow at `.github/workflows/ci.yml` which
  runs `ruff`, `black --check`, and the test suite on push and PR to `main`.

**Packaging the `lastping_sdk`**

This repository includes a minimal SDK in `lastping_sdk/` and a PEP-621 `pyproject.toml` so
the SDK can be built and published independently.

Build locally:

```bash
python -m pip install --upgrade build
python -m build lastping_sdk
```

Publish to PyPI (recommended via CI):

1. Create a GitHub release or push to `main`.
2. Add `PYPI_USERNAME` and `PYPI_PASSWORD` to the repository's GitHub Secrets (Settings → Secrets → Actions).
3. The included workflow `.github/workflows/publish_lastping_sdk.yml` will build and upload the wheel/sdist.

Alternatively, publish manually:

```bash
python -m pip install --upgrade twine
python -m twine upload lastping_sdk/dist/*
```

If you want the CI to run async tests, install dev requirements which include `aiohttp`:

```bash
pip install -r requirements.txt
```

**Tests**

Unit tests live under `tests/`. Run `python -m pytest -q` to run them locally. The project includes
basic tests for alerts and worker behaviour; expand tests when adding features.

Quick note: use the project virtualenv when available. You can run the tests using the provided
helper scripts which prefer the repo `.venv`:

Windows PowerShell:

```powershell
scripts\run_tests.ps1
```

macOS / Linux:

```bash
scripts/run_tests.sh
```

**Admin bypass & distributed rate limiting**

- Admin bypass: an `X-ADMIN-TOKEN` header matching the `ADMIN_TOKEN` env var will bypass project API key checks and rate limits for management endpoints.
- Distributed deployments: to enable robust per-API-key rate limiting across multiple worker instances, set `REDIS_URL` and install the `redis` package. The worker will use Redis counters when available; otherwise it falls back to a DB-backed per-minute counter.
  Install Redis and set `REDIS_URL` in production:

```bash
pip install redis
# example env var for local redis
export REDIS_URL=redis://localhost:6379/0
```

**Contributing**

Please open issues or PRs. Follow the existing coding style and run tests + linters before submitting.

**License**

See the `LICENSE` file at the repository root.

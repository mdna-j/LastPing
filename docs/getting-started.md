# Getting Started

This guide expands on the quickstart in `README.md` and shows how to run the API and worker
locally for development.

1. Create virtualenv and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Initialize the database (example using SQLite for local dev):

```bash
export DATABASE_URL=sqlite+aiosqlite:///./test.db
# run alembic migrations or create tables via app startup
```

3. Run API and worker in separate terminals:

```bash
uvicorn src.main:app --reload
python -m src.worker
```

Notes:

- For PostgreSQL use a `DATABASE_URL` like `postgresql+asyncpg://user:pass@localhost/dbname`.
- Use `docker-compose` for a reproducible local stack.

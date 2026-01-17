# Getting Started

This guide expands on the quickstart in `README.md` and shows how to run the API and worker
locally for development.

1. Create a virtual environment and install dependencies

Windows PowerShell:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux (bash):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Initialize the database (example using SQLite for local development)

Set the `DATABASE_URL` and run migrations (recommended):

Linux / macOS:

```bash
export DATABASE_URL=sqlite:///./test.db
alembic upgrade head
```

Windows PowerShell:

```powershell
$env:DATABASE_URL = 'sqlite:///./test.db'
python -m alembic upgrade head
```

If you prefer to create tables directly from the SQLModel metadata (development only):

```bash
python -c "from src.db import create_db_and_tables; create_db_and_tables()"
```

3. Run the API and worker in separate terminals

```bash
uvicorn src.main:app --reload
python -m src.worker
```

Notes:

- For PostgreSQL use a `DATABASE_URL` like `postgresql+asyncpg://user:pass@localhost/dbname`.
- Use `docker-compose` for a reproducible local stack.

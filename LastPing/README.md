# LastPing

Lightweight uptime and heartbeat monitoring service. This repository contains the initial scaffold for LastPing (FastAPI backend, worker, and SDK).

Tech highlights:
- Python 3.11
- FastAPI, Uvicorn, Pydantic
- PostgreSQL, Redis
- Docker / Docker Compose for local development

Quick start (development):

1. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the API locally:

```powershell
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

License: MIT

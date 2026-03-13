"""
Database helpers for LastPing.

Defines the SQLModel `engine` and a `get_session` generator used by
FastAPI dependencies and the background worker. `create_db_and_tables`
is a convenience for local development only.
"""

import os
from typing import Generator

from sqlmodel import SQLModel, create_engine, Session

DEFAULT_DATABASE_URL = "sqlite:///./dev.db"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _build_engine(database_url: str):
    return create_engine(database_url, echo=False)


# Allow overriding via `DATABASE_URL` environment variable.
DATABASE_URL = get_database_url()

# echo SQL for debugging when needed
engine = _build_engine(DATABASE_URL)


def ensure_engine():
    global DATABASE_URL, engine
    latest = get_database_url()
    if latest != DATABASE_URL:
        try:
            engine.dispose()
        except Exception:
            pass
        DATABASE_URL = latest
        engine = _build_engine(DATABASE_URL)
    return engine


def dispose_engine():
    global engine
    try:
        engine.dispose()
    except Exception:
        pass


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request/worker use.

    Use this as a FastAPI dependency: `session: Session = Depends(get_session)`.
    """
    with Session(ensure_engine()) as session:
        yield session


def create_db_and_tables():
    """Create all tables from SQLModel metadata (development helper)."""
    SQLModel.metadata.create_all(ensure_engine())

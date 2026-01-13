"""
Database helpers for LastPing.

Defines the SQLModel `engine` and a `get_session` generator used by
FastAPI dependencies and the background worker. `create_db_and_tables`
is a convenience for local development only.
"""

import os
from typing import Generator

from sqlmodel import SQLModel, create_engine, Session

# Allow overriding via `DATABASE_URL` environment variable.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

# echo SQL for debugging when needed
engine = create_engine(DATABASE_URL, echo=False)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request/worker use.

    Use this as a FastAPI dependency: `session: Session = Depends(get_session)`.
    """
    with Session(engine) as session:
        yield session


def create_db_and_tables():
    """Create all tables from SQLModel metadata (development helper)."""
    SQLModel.metadata.create_all(engine)

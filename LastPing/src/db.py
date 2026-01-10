import os
from typing import Generator

from sqlmodel import SQLModel, create_engine, Session

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

# echo SQL for debugging when needed
engine = create_engine(DATABASE_URL, echo=False)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

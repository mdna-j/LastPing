"""Bootstrap a fresh database using SQLModel metadata, then stamp Alembic head.

This is intended for empty databases in local/dev environments to avoid
manual create_all + stamp steps.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure models are imported so SQLModel metadata is populated.
import src.models  # noqa: F401


def _get_head_revision() -> str:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if not head:
        raise RuntimeError("No Alembic head revision found.")
    return head


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set; bootstrap skipped.", file=sys.stderr)
        return 0

    engine = create_engine(db_url)
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = [t for t in inspector.get_table_names() if t != "alembic_version"]
        if tables:
            # Database already initialized.
            return 0

        # Create tables from current metadata.
        SQLModel.metadata.create_all(conn)

        # Stamp Alembic head so migrations are in sync.
        head = _get_head_revision()
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(255) NOT NULL)")
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

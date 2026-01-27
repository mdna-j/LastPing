import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text, create_engine

from alembic import context

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Prefer DATABASE_URL when provided (e.g. Docker/Postgres), otherwise fall back to alembic.ini.
db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Interpret the config file for Python logging.
try:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
except Exception:
    # ignore logging config errors in lightweight environments
    pass

# import models so SQLModel metadata is populated
from sqlmodel import SQLModel
import src.models  # noqa: F401

target_metadata = SQLModel.metadata


def run_migrations_offline():
    url = db_url or config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    if db_url:
        connectable = create_engine(db_url, poolclass=pool.NullPool)
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    with connectable.connect() as connection:
        # Ensure alembic_version can store long revision ids (Postgres)
        if connection.dialect.name == "postgresql":
            try:
                exists = connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
                if exists:
                    connection.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"))
                else:
                    connection.execute(
                        text(
                            "CREATE TABLE IF NOT EXISTS alembic_version ("
                            "version_num VARCHAR(255) NOT NULL"
                            ")"
                        )
                    )
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

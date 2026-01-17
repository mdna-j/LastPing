"""Reset alembic_version and show before/after.

This script prints current alembic_version rows, deletes them, and prints afterwards.
"""
from sqlalchemy import create_engine, text
import os

url = os.environ.get('DATABASE_URL', 'sqlite:///./dev.db')
print('Using DB URL:', url)
engine = create_engine(url)
with engine.connect() as conn:
    try:
        rows = conn.execute(text('SELECT * FROM alembic_version')).fetchall()
        print('BEFORE alembic_version rows:', rows)
    except Exception as e:
        print('BEFORE alembic_version query failed:', e)
    try:
        conn.execute(text('DELETE FROM alembic_version'))
        conn.commit()
        print('Deleted alembic_version rows')
    except Exception as e:
        print('Failed to delete alembic_version:', e)
    try:
        rows = conn.execute(text('SELECT * FROM alembic_version')).fetchall()
        print('AFTER alembic_version rows:', rows)
    except Exception as e:
        print('AFTER alembic_version query failed:', e)
print('Done')

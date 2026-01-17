"""Drop specific conflicting tables before running migrations.
"""
from sqlalchemy import create_engine, text
import os

url = os.environ.get('DATABASE_URL', 'sqlite:///./dev.db')
print('Using DB URL:', url)
engine = create_engine(url)
with engine.connect() as conn:
    try:
        conn.execute(text('DROP TABLE IF EXISTS user_usage'))
        conn.commit()
        print('Dropped table: user_usage')
    except Exception as e:
        print('Error dropping user_usage:', e)
print('Done')

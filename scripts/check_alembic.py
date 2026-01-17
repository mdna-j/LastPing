from sqlalchemy import create_engine, text
import os
url = os.environ.get('DATABASE_URL', 'sqlite:///./dev.db')
print('Using DB URL:', url)
engine = create_engine(url)
with engine.connect() as conn:
    try:
        rows = conn.execute(text('select * from alembic_version')).fetchall()
        print('alembic_version rows:', rows)
    except Exception as e:
        print('alembic_version query failed:', e)

# list incident columns if exists
from sqlalchemy import inspect
insp = inspect(engine)
if 'incident' in insp.get_table_names():
    cols = [c['name'] for c in insp.get_columns('incident')]
    print('incident columns:', cols)
else:
    print('incident table not present')

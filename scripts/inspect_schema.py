import sqlite3, json
conn = sqlite3.connect('dev.db')
print('alembic_version:', list(conn.execute("SELECT * FROM alembic_version")))
row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='project'").fetchone()
print('project SQL:')
print(row[0] if row else 'not found')
cols = [r[1] for r in conn.execute("PRAGMA table_info('project')")]
print('cols:', json.dumps(cols))

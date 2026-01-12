import sqlite3, json
conn = sqlite3.connect('dev.db')
cols = [r[1] for r in conn.execute("PRAGMA table_info('project')")] 
print(json.dumps(cols))

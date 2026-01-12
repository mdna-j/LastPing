import sqlite3
import json

conn = sqlite3.connect('dev.db')
cur = conn.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info('project')")]
print('before cols:', cols)
if 'api_key' not in cols:
    print('api_key not present, nothing to do')
else:
    keep = [
        'id', 'name', 'api_key_hash', 'created_at', 'owner_email',
        'alert_rate_limit_count', 'alert_rate_limit_window', 'last_escalated_at',
        'discord_webhook_url', 'slack_webhook_url', 'pagerduty_integration_key', 'generic_webhook_url'
    ]
    keep_existing = [c for c in keep if c in cols]
    cols_csv = ','.join(keep_existing)
    cur.execute('DROP TABLE IF EXISTS project_new')
    cur.execute('''CREATE TABLE project_new (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        api_key_hash TEXT,
        created_at DATETIME,
        owner_email TEXT,
        alert_rate_limit_count INTEGER,
        alert_rate_limit_window INTEGER,
        last_escalated_at DATETIME,
        discord_webhook_url TEXT,
        slack_webhook_url TEXT,
        pagerduty_integration_key TEXT,
        generic_webhook_url TEXT
    )''')
    cur.execute(f'INSERT INTO project_new ({cols_csv}) SELECT {cols_csv} FROM project')
    cur.execute('DROP TABLE project')
    cur.execute('ALTER TABLE project_new RENAME TO project')
    conn.commit()
    cols2 = [r[1] for r in cur.execute("PRAGMA table_info('project')")]
    print('after cols:', cols2)
conn.close()

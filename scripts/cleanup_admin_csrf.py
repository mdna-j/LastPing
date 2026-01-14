#!/usr/bin/env python3
"""Cleanup expired admin CSRF tokens from the database.

Run periodically (cron) if `ADMIN_CSRF_SERVER_SIDE=1` is enabled.
"""
from sqlmodel import Session, select
from src import db as dbmod
from src.models import AdminCsrf
from datetime import datetime


def cleanup():
    dbmod.create_db_and_tables()
    with Session(dbmod.engine) as session:
        now = datetime.utcnow()
        rows = session.exec(select(AdminCsrf).where(AdminCsrf.expires_at < now)).all()
        for r in rows:
            session.delete(r)
        session.commit()
        print(f"Deleted {len(rows)} expired admin_csrf tokens")


if __name__ == '__main__':
    cleanup()

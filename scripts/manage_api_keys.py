#!/usr/bin/env python3
"""CLI to manage API keys for projects.

Usage:
  manage_api_keys.py create <project_id> [--limit N]
  manage_api_keys.py rotate-primary <project_id>

- `create` creates an ApiKey record for a project and prints the plaintext key.
- `rotate-primary` replaces the project's primary `api_key_hash` (used by legacy endpoints) and prints the new key.

This script should be run from the repository root where the app's `src` package is importable.
"""

import argparse
from sqlmodel import Session
from src import db as dbmod
from src.models import ApiKey, Project
from src.security import generate_api_key, hash_api_key
from src.models import AuditLog


def create_api_key(project_id: int, limit: int | None):
    dbmod.create_db_and_tables()
    with Session(dbmod.engine) as session:
        project = session.get(Project, project_id)
        if not project:
            print(f"Project {project_id} not found")
            return 2
        plain = generate_api_key()
        ak = ApiKey(project_id=project_id, key_hash=hash_api_key(plain), rate_limit_per_minute=limit or 0)
        session.add(ak)
        session.commit()
        session.refresh(ak)
        # audit
        al = AuditLog(actor="cli", action="create_apikey", target_type="project", target_id=project_id, details=f"apikey_id={ak.id}", actor_ip=None, user_agent=None)
        session.add(al)
        session.commit()
        print(plain)
        return 0


def rotate_primary(project_id: int):
    dbmod.create_db_and_tables()
    with Session(dbmod.engine) as session:
        project = session.get(Project, project_id)
        if not project:
            print(f"Project {project_id} not found")
            return 2
        new = generate_api_key()
        project.api_key_hash = hash_api_key(new)
        session.add(project)
        session.commit()
        # audit
        al = AuditLog(actor="cli", action="rotate_primary_api_key", target_type="project", target_id=project_id, details=None, actor_ip=None, user_agent=None)
        session.add(al)
        session.commit()
        print(new)
        return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd')

    p_create = sub.add_parser('create')
    p_create.add_argument('project_id', type=int)
    p_create.add_argument('--limit', type=int, default=0, help='rate limit per minute (0 = unlimited)')

    p_rot = sub.add_parser('rotate-primary')
    p_rot.add_argument('project_id', type=int)

    args = parser.parse_args()
    if args.cmd == 'create':
        raise SystemExit(create_api_key(args.project_id, args.limit))
    if args.cmd == 'rotate-primary':
        raise SystemExit(rotate_primary(args.project_id))
    parser.print_help()


if __name__ == '__main__':
    main()

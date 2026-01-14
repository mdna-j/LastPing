"""Admin CLI for simple user management.

Usage:
  python scripts/manage_users.py create --email alice@example.com --password secret
  python scripts/manage_users.py add-member --project 1 --email bob@example.com --role owner
"""
import argparse
from sqlmodel import Session, select
from src.db import engine
from src.models import User, Project, ProjectMembership
from src.security import hash_password


def create_user(email: str, password: str):
    with Session(engine) as s:
        existing = s.exec(select(User).where(User.email == email)).first()
        if existing:
            print("User already exists")
            return
        u = User(email=email, hashed_password=hash_password(password), is_active=True)
        s.add(u)
        s.commit()
        s.refresh(u)
        print("Created user", u.id)


def add_member(project_id: int, email: str, role: str = "viewer"):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).first()
        if not user:
            print("User not found; create user first")
            return
        proj = s.get(Project, project_id)
        if not proj:
            print("Project not found")
            return
        existing = s.exec(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)).first()
        if existing:
            print("User already a member")
            return
        pm = ProjectMembership(user_id=user.id, project_id=project_id, role=role)
        s.add(pm)
        s.commit()
        print("Added membership")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')
    c1 = sub.add_parser('create')
    c1.add_argument('--email', required=True)
    c1.add_argument('--password', required=True)
    c2 = sub.add_parser('add-member')
    c2.add_argument('--project', type=int, required=True)
    c2.add_argument('--email', required=True)
    c2.add_argument('--role', default='viewer')
    args = p.parse_args()
    if args.cmd == 'create':
        create_user(args.email, args.password)
    elif args.cmd == 'add-member':
        add_member(args.project, args.email, args.role)
    else:
        p.print_help()

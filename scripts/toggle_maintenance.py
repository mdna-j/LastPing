#!/usr/bin/env python3
"""CLI to set or clear maintenance windows for projects or checks.

Usage examples:

# set project maintenance
python scripts/toggle_maintenance.py --project 1 --start 2026-01-14T12:00:00 --end 2026-01-14T13:00:00

# clear check maintenance
python scripts/toggle_maintenance.py --check 2 --clear
"""
from datetime import datetime
import argparse
import sys

from sqlmodel import Session

from src.db import engine
from src.models import Project, Check


def parse_dt(v):
    if v is None:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        raise argparse.ArgumentTypeError("invalid datetime; use ISO format YYYY-MM-DDTHH:MM:SS")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project', type=int)
    p.add_argument('--check', type=int)
    p.add_argument('--start', type=parse_dt)
    p.add_argument('--end', type=parse_dt)
    p.add_argument('--clear', action='store_true')
    args = p.parse_args()

    if not args.project and not args.check:
        print("Specify --project or --check", file=sys.stderr)
        sys.exit(2)

    with Session(engine) as session:
        if args.project:
            proj = session.get(Project, args.project)
            if not proj:
                print("Project not found", file=sys.stderr)
                sys.exit(1)
            if args.clear:
                proj.maintenance_starts_at = None
                proj.maintenance_ends_at = None
            else:
                proj.maintenance_starts_at = args.start
                proj.maintenance_ends_at = args.end
            session.add(proj)
            session.commit()
            print("Project maintenance updated")

        if args.check:
            chk = session.get(Check, args.check)
            if not chk:
                print("Check not found", file=sys.stderr)
                sys.exit(1)
            if args.clear:
                chk.maintenance_starts_at = None
                chk.maintenance_ends_at = None
            else:
                chk.maintenance_starts_at = args.start
                chk.maintenance_ends_at = args.end
            session.add(chk)
            session.commit()
            print("Check maintenance updated")


if __name__ == '__main__':
    main()

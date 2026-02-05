#!/usr/bin/env python3
"""Train and store predictive models for a project/check.

Examples:
  py -3.11 scripts/train_predictive_models.py --project-id 1
  py -3.11 scripts/train_predictive_models.py --project-id 1 --check-id 12 --days 14 --min-events 5
"""
import argparse
import sys
from pathlib import Path

from sqlmodel import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import Project, Check
from src.predictive_models import train_seasonal_hourly_models


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--check-id", type=int)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--min-events", type=int, default=10)
    args = p.parse_args()

    with Session(engine) as session:
        proj = session.get(Project, args.project_id)
        if not proj:
            print("Project not found", file=sys.stderr)
            sys.exit(1)
        if args.check_id:
            chk = session.get(Check, args.check_id)
            if not chk or chk.project_id != args.project_id:
                print("Check not found in project", file=sys.stderr)
                sys.exit(1)

        models = train_seasonal_hourly_models(
            session=session,
            project_id=args.project_id,
            check_id=args.check_id,
            days=args.days,
            min_events=args.min_events,
        )
        print(f"Trained {len(models)} model(s).")
        for m in models:
            print(f"- model_id={m.id} check_id={m.check_id} version={m.version} trained_at={m.trained_at.isoformat()}")


if __name__ == "__main__":
    main()

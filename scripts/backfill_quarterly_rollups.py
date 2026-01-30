"""
Backfill quarterly availability rollups from monthly rollups.

This uses AvailabilityRollup rows where period_type="month".

Example:
  py -3.11 scripts/backfill_quarterly_rollups.py --start 2024-01-01 --end 2026-01-01
  py -3.11 scripts/backfill_quarterly_rollups.py --project-id 1 --dry-run
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import AvailabilityRollup, Project


def _quarter_start(dt: datetime) -> datetime:
    q = (dt.month - 1) // 3
    month = q * 3 + 1
    return dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_quarter(dt: datetime) -> datetime:
    month = dt.month + 3
    year = dt.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return dt.replace(year=year, month=month, day=1)


def _parse_dt(val):
    if not val:
        return None
    return datetime.fromisoformat(val)


def _quarter_label(dt: datetime) -> str:
    qnum = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{qnum}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, default=None)
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start_arg = _parse_dt(args.start)
    end_arg = _parse_dt(args.end)

    with Session(engine) as session:
        if args.project_id:
            proj = session.get(Project, args.project_id)
            projects = [proj] if proj else []
        else:
            projects = session.exec(select(Project)).all()

        for project in projects:
            if not project:
                continue
            stmt = select(AvailabilityRollup).where(
                AvailabilityRollup.project_id == project.id,
                AvailabilityRollup.period_type == "month",
            )
            monthly = session.exec(stmt).all()
            if not monthly:
                continue

            min_start = min(r.period_start for r in monthly)
            max_end = max(r.period_end for r in monthly)
            start = _quarter_start(start_arg or min_start)
            end = _quarter_start(end_arg or max_end)

            cur = start
            while cur <= end:
                qend = _next_quarter(cur)
                label = _quarter_label(cur)

                rows = [r for r in monthly if r.period_start >= cur and r.period_end <= qend]
                if not rows:
                    cur = qend
                    continue

                by_check = {}
                for r in rows:
                    by_check.setdefault(r.check_id, []).append(r)

                for check_id, items in by_check.items():
                    existing = session.exec(
                        select(AvailabilityRollup).where(
                            AvailabilityRollup.project_id == project.id,
                            AvailabilityRollup.check_id == check_id,
                            AvailabilityRollup.period_type == "quarter",
                            AvailabilityRollup.period == label,
                        )
                    ).first()
                    if existing:
                        continue
                    avg = sum(i.uptime_percent for i in items) / len(items)
                    slo_met = (project.slo_target is not None and avg >= project.slo_target) if project.slo_target is not None else None
                    sla_met = (project.sla_target is not None and avg >= project.sla_target) if project.sla_target is not None else None
                    if args.dry_run:
                        print(f"[dry-run] project {project.id} check {check_id} quarter {label}")
                        continue
                    row = AvailabilityRollup(
                        project_id=project.id,
                        check_id=check_id,
                        period_type="quarter",
                        period=label,
                        period_start=cur,
                        period_end=qend,
                        uptime_percent=avg,
                        slo_met=slo_met,
                        sla_met=sla_met,
                    )
                    session.add(row)
                    session.commit()

                cur = qend

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

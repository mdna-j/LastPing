"""
Backfill monthly availability rollups from existing UptimeSnapshot data.

Example:
  py -3.11 scripts/backfill_rollups.py --start 2025-01-01 --end 2026-01-01
  py -3.11 scripts/backfill_rollups.py --project-id 1 --dry-run
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import Project, Check, UptimeSnapshot, AvailabilityRollup


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime) -> datetime:
    year = dt.year + (dt.month // 12)
    month = (dt.month % 12) + 1
    return dt.replace(year=year, month=month, day=1)


def _compute_monthly_rollups(session: Session, project: Project, month_start: datetime, month_end: datetime):
    checks = session.exec(select(Check).where(Check.project_id == project.id)).all()
    rollups = []
    for chk in checks:
        snaps = session.exec(
            select(UptimeSnapshot)
            .where(
                UptimeSnapshot.project_id == project.id,
                UptimeSnapshot.check_id == chk.id,
                UptimeSnapshot.window_end >= month_start,
                UptimeSnapshot.window_end < month_end,
            )
            .order_by(UptimeSnapshot.window_end.desc())
        ).all()
        latest = {}
        for s in snaps:
            day = s.window_end.date().isoformat()
            if day not in latest:
                latest[day] = s
        vals = [s.uptime_percent for s in latest.values()]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        rollups.append({
            "check_id": chk.id,
            "uptime_percent": avg,
            "slo_met": (project.slo_target is not None and avg >= project.slo_target) if project.slo_target is not None else None,
            "sla_met": (project.sla_target is not None and avg >= project.sla_target) if project.sla_target is not None else None,
        })

    if rollups:
        agg = sum([r["uptime_percent"] for r in rollups]) / len(rollups)
        rollups.append({
            "check_id": None,
            "uptime_percent": agg,
            "slo_met": (project.slo_target is not None and agg >= project.slo_target) if project.slo_target is not None else None,
            "sla_met": (project.sla_target is not None and agg >= project.sla_target) if project.sla_target is not None else None,
        })
    return rollups


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    return datetime.fromisoformat(val)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, default=None)
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start_arg = _parse_dt(args.start)
    end_arg = _parse_dt(args.end)

    with Session(engine) as session:
        projects = []
        if args.project_id:
            proj = session.get(Project, args.project_id)
            if proj:
                projects = [proj]
        else:
            projects = session.exec(select(Project)).all()

        for project in projects:
            snap_dates = session.exec(
                select(UptimeSnapshot.window_end).where(UptimeSnapshot.project_id == project.id)
            ).all()
            if not snap_dates:
                continue
            min_end = min(snap_dates)
            max_end = max(snap_dates)
            start = _month_start(start_arg or min_end)
            end = _month_start(end_arg or max_end)

            cur = start
            while cur <= end:
                next_month = _next_month(cur)
                period = cur.strftime("%Y-%m")
                rollups = _compute_monthly_rollups(session, project, cur, next_month)
                for r in rollups:
                    existing = session.exec(
                        select(AvailabilityRollup).where(
                            AvailabilityRollup.project_id == project.id,
                            AvailabilityRollup.check_id == r["check_id"],
                            AvailabilityRollup.period_type == "month",
                            AvailabilityRollup.period == period,
                        )
                    ).first()
                    if existing:
                        continue
                    if args.dry_run:
                        print(f"[dry-run] project {project.id} check {r['check_id']} period {period}")
                        continue
                    row = AvailabilityRollup(
                        project_id=project.id,
                        check_id=r["check_id"],
                        period_type="month",
                        period=period,
                        period_start=cur,
                        period_end=next_month,
                        uptime_percent=r["uptime_percent"],
                        slo_met=r["slo_met"],
                        sla_met=r["sla_met"],
                    )
                    session.add(row)
                    session.commit()
                cur = next_month


if __name__ == "__main__":
    main()

"""
Archive monthly (and optional quarterly) availability rollups.

Designed for cron/CI:
  py -3.11 scripts/archive_rollups.py
  py -3.11 scripts/archive_rollups.py --quarterly
  py -3.11 scripts/archive_rollups.py --project-id 1 --dry-run
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import Project, Check, UptimeSnapshot, AvailabilityRollup


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _quarter_start(dt: datetime) -> datetime:
    q = (dt.month - 1) // 3
    month = q * 3 + 1
    return dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _compute_rollups_for_range(session: Session, project: Project, start: datetime, end: datetime):
    checks = session.exec(select(Check).where(Check.project_id == project.id)).all()
    rollups = []
    for chk in checks:
        snaps = session.exec(
            select(UptimeSnapshot)
            .where(
                UptimeSnapshot.project_id == project.id,
                UptimeSnapshot.check_id == chk.id,
                UptimeSnapshot.window_end >= start,
                UptimeSnapshot.window_end <= end,
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


def _archive_period(session: Session, project: Project, period_type: str, start: datetime, end: datetime, period_label: str, dry_run: bool):
    rollups = _compute_rollups_for_range(session, project, start, end)
    if not rollups:
        return 0
    inserted = 0
    for r in rollups:
        existing = session.exec(
            select(AvailabilityRollup).where(
                AvailabilityRollup.project_id == project.id,
                AvailabilityRollup.check_id == r["check_id"],
                AvailabilityRollup.period_type == period_type,
                AvailabilityRollup.period == period_label,
            )
        ).first()
        if existing:
            continue
        if dry_run:
            print(f"[dry-run] project {project.id} check {r['check_id']} {period_type} {period_label}")
            continue
        row = AvailabilityRollup(
            project_id=project.id,
            check_id=r["check_id"],
            period_type=period_type,
            period=period_label,
            period_start=start,
            period_end=end,
            uptime_percent=r["uptime_percent"],
            slo_met=r["slo_met"],
            sla_met=r["sla_met"],
        )
        session.add(row)
        session.commit()
        inserted += 1
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, default=None)
    ap.add_argument("--quarterly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.utcnow()
    cur_month = _month_start(now)
    prev_month_end = cur_month
    prev_month_start = _month_start(cur_month - timedelta(days=1))
    month_label = prev_month_start.strftime("%Y-%m")

    cur_quarter = _quarter_start(now)
    prev_quarter_end = cur_quarter
    prev_quarter_start = _quarter_start(cur_quarter - timedelta(days=1))
    qnum = (prev_quarter_start.month - 1) // 3 + 1
    quarter_label = f"{prev_quarter_start.year}-Q{qnum}"

    with Session(engine) as session:
        if args.project_id:
            proj = session.get(Project, args.project_id)
            projects = [proj] if proj else []
        else:
            projects = session.exec(select(Project)).all()

        for project in projects:
            if not project:
                continue
            _archive_period(session, project, "month", prev_month_start, prev_month_end, month_label, args.dry_run)
            if args.quarterly:
                _archive_period(session, project, "quarter", prev_quarter_start, prev_quarter_end, quarter_label, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

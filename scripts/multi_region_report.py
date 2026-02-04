#!/usr/bin/env python3
"""Generate a multi-region lease/health report for validation.

Examples:
  py -3.11 scripts/multi_region_report.py
  py -3.11 scripts/multi_region_report.py --recent-hours 2 --down-threshold 3
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from typing import List

from sqlmodel import Session, select

from src.db import engine
from src.models import Check, CheckLease, Event


def _now() -> datetime:
    return datetime.utcnow()


def _parse_regions(region: str | None) -> List[str]:
    if not region:
        return []
    low = region.strip().lower()
    if low in ("*", "all"):
        return []
    parts = [p.strip() for p in region.replace(",", " ").split() if p.strip()]
    return parts


def _owner_matches_region(owner: str | None, regions: List[str]) -> bool:
    if not regions:
        return True
    if not owner:
        return False
    low = owner.lower()
    return any(r.lower() in low for r in regions)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--recent-hours", type=int, default=1, help="event window in hours")
    p.add_argument("--down-threshold", type=int, default=3, help="down-event threshold for highlighting")
    args = p.parse_args()

    now = _now()
    since = now - timedelta(hours=max(args.recent_hours, 1))

    with Session(engine) as session:
        checks = session.exec(select(Check)).all()
        leases = session.exec(select(CheckLease)).all()
        events = session.exec(
            select(Event).where(Event.created_at >= since)
        ).all()

    total_checks = len(checks)
    pinned = [c for c in checks if (c.region or "").strip() not in ("", None)]
    wildcards = [c for c in checks if (c.region or "").strip().lower() in ("*", "all")]
    multi = [c for c in pinned if len(_parse_regions(c.region)) > 1]

    print("Multi-region report")
    print(f"  checks: total={total_checks} pinned={len(pinned)} wildcard={len(wildcards)} multi-region={len(multi)}")

    owner_counts = Counter([l.lease_owner or "unknown" for l in leases])
    expired = [l for l in leases if l.lease_expires_at and l.lease_expires_at < now]
    print(f"  leases: total={len(leases)} expired={len(expired)}")
    for owner, cnt in owner_counts.most_common():
        print(f"    - {owner}: {cnt}")

    mismatches = []
    for lease in leases:
        chk = next((c for c in checks if c.id == lease.check_id), None)
        if not chk:
            continue
        regions = _parse_regions(chk.region)
        if regions and not _owner_matches_region(lease.lease_owner, regions):
            mismatches.append((chk.id, chk.region, lease.lease_owner))
    if mismatches:
        print("  lease/region mismatches:")
        for cid, region, owner in mismatches[:20]:
            print(f"    - check {cid} region='{region}' leased_by='{owner}'")
    else:
        print("  lease/region mismatches: none")

    down_events = [e for e in events if e.event_type in ("down", "http_failure")]
    down_counts = Counter([e.check_id for e in down_events])
    noisy = [(cid, cnt) for cid, cnt in down_counts.items() if cnt >= args.down_threshold]
    if noisy:
        print(f"  checks with >= {args.down_threshold} down events in last {args.recent_hours}h:")
        for cid, cnt in sorted(noisy, key=lambda x: x[1], reverse=True)[:20]:
            print(f"    - check {cid}: {cnt}")
    else:
        print(f"  checks with >= {args.down_threshold} down events in last {args.recent_hours}h: none")


if __name__ == "__main__":
    main()

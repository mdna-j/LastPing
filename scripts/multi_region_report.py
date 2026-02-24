#!/usr/bin/env python3
"""Generate a multi-region lease/health report for validation.

Examples:
  py -3.11 scripts/multi_region_report.py
  py -3.11 scripts/multi_region_report.py --recent-hours 2 --down-threshold 3
  py -3.11 scripts/multi_region_report.py --json-out artifacts/multi_region_report.json --markdown-out artifacts/multi_region_signoff.md --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import Check, CheckLease, Event


def _now() -> datetime:
    return datetime.utcnow()


def _parse_regions(region: str | None) -> list[str]:
    if not region:
        return []
    low = region.strip().lower()
    if low in ("*", "all"):
        return []
    return [p.strip() for p in region.replace(",", " ").split() if p.strip()]


def _owner_matches_region(owner: str | None, regions: list[str]) -> bool:
    if not regions:
        return True
    if not owner:
        return False
    low = owner.lower()
    return any(r.lower() in low for r in regions)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checks = payload["checks"]
    leases = payload["leases"]
    recent = payload["recent_window"]
    acceptance = payload["acceptance"]
    noisy = payload["noisy_down_checks"]
    mismatch_rows = payload["mismatch_samples"]
    owner_counts = payload["owner_counts"]

    lines = [
        "# Multi-Region Staging Signoff",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Environment: `{payload['environment']}`",
        f"- Commit SHA: `{payload['git_sha']}`",
        f"- Overall status: **{'PASS' if acceptance['all_passed'] else 'FAIL'}**",
        "",
        "## Acceptance Criteria",
        f"- Lease region mismatches: `{acceptance['lease_region_mismatch_count']}`",
        f"- Expired leases allowed: `{acceptance['max_expired_leases_allowed']}`",
        f"- Expired leases observed: `{acceptance['expired_lease_count']}`",
        "",
        "## Snapshot",
        f"- Checks: total `{checks['total']}`, pinned `{checks['pinned']}`, wildcard `{checks['wildcard']}`, multi-region `{checks['multi_region']}`",
        f"- Leases: total `{leases['total']}`, expired `{leases['expired']}`",
        f"- Recent window: `{recent['hours']}h` with down threshold `{recent['down_threshold']}`",
        "",
        "## Lease Owners",
    ]

    if owner_counts:
        lines.extend([f"- `{owner}`: `{count}`" for owner, count in owner_counts])
    else:
        lines.append("- None")

    lines.append("")
    lines.append("## Mismatch Samples")
    if mismatch_rows:
        lines.append("| Check ID | Region | Lease Owner |")
        lines.append("|---|---|---|")
        for row in mismatch_rows:
            lines.append(f"| {row['check_id']} | `{row['region']}` | `{row['lease_owner']}` |")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("## Noisy Checks")
    if noisy:
        lines.append("| Check ID | Down Events |")
        lines.append("|---|---|")
        for row in noisy:
            lines.append(f"| {row['check_id']} | {row['count']} |")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Signoff")
    lines.append("- Reviewer: `TBD`")
    lines.append("- Decision: `TBD`")
    lines.append("- Notes: `TBD`")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _git_sha() -> str:
    return os.environ.get("GITHUB_SHA", "unknown")


def _build_payload(
    *,
    now: datetime,
    recent_hours: int,
    down_threshold: int,
    max_expired_leases: int,
    environment: str,
) -> dict[str, Any]:
    since = now - timedelta(hours=max(recent_hours, 1))

    with Session(engine) as session:
        checks = session.exec(select(Check)).all()
        leases = session.exec(select(CheckLease)).all()
        events = session.exec(select(Event).where(Event.created_at >= since)).all()

    check_by_id = {c.id: c for c in checks}
    pinned = [c for c in checks if (c.region or "").strip()]
    wildcards = [c for c in checks if (c.region or "").strip().lower() in ("*", "all")]
    multi = [c for c in pinned if len(_parse_regions(c.region)) > 1]

    owner_counts = Counter([l.lease_owner or "unknown" for l in leases])
    owner_rows = [{"owner": owner, "count": count} for owner, count in owner_counts.most_common()]
    expired = [l for l in leases if l.lease_expires_at and l.lease_expires_at < now]

    mismatches = []
    for lease in leases:
        chk = check_by_id.get(lease.check_id)
        if not chk:
            continue
        regions = _parse_regions(chk.region)
        if regions and not _owner_matches_region(lease.lease_owner, regions):
            mismatches.append(
                {
                    "check_id": chk.id,
                    "region": chk.region or "",
                    "lease_owner": lease.lease_owner or "unknown",
                }
            )

    down_events = [e for e in events if e.event_type in ("down", "http_failure")]
    down_counts = Counter([e.check_id for e in down_events])
    noisy = [{"check_id": cid, "count": cnt} for cid, cnt in sorted(down_counts.items(), key=lambda x: x[1], reverse=True) if cnt >= down_threshold]

    acceptance = {
        "lease_region_mismatch_count": len(mismatches),
        "expired_lease_count": len(expired),
        "max_expired_leases_allowed": max_expired_leases,
    }
    acceptance["all_passed"] = (
        acceptance["lease_region_mismatch_count"] == 0
        and acceptance["expired_lease_count"] <= acceptance["max_expired_leases_allowed"]
    )

    return {
        "generated_at": now.isoformat() + "Z",
        "environment": environment,
        "git_sha": _git_sha(),
        "checks": {
            "total": len(checks),
            "pinned": len(pinned),
            "wildcard": len(wildcards),
            "multi_region": len(multi),
        },
        "leases": {
            "total": len(leases),
            "expired": len(expired),
        },
        "owner_counts": owner_rows,
        "mismatch_samples": mismatches[:20],
        "noisy_down_checks": noisy[:20],
        "recent_window": {
            "hours": recent_hours,
            "down_threshold": down_threshold,
        },
        "acceptance": acceptance,
    }


def _print_console(payload: dict[str, Any]) -> None:
    checks = payload["checks"]
    leases = payload["leases"]
    recent = payload["recent_window"]
    acceptance = payload["acceptance"]

    print("Multi-region report")
    print(
        f"  checks: total={checks['total']} pinned={checks['pinned']} wildcard={checks['wildcard']} "
        f"multi-region={checks['multi_region']}"
    )
    print(f"  leases: total={leases['total']} expired={leases['expired']}")
    if payload["owner_counts"]:
        for row in payload["owner_counts"]:
            print(f"    - {row['owner']}: {row['count']}")

    if payload["mismatch_samples"]:
        print("  lease/region mismatches:")
        for row in payload["mismatch_samples"]:
            print(
                f"    - check {row['check_id']} region='{row['region']}' leased_by='{row['lease_owner']}'"
            )
    else:
        print("  lease/region mismatches: none")

    if payload["noisy_down_checks"]:
        print(
            f"  checks with >= {recent['down_threshold']} down events in last {recent['hours']}h:"
        )
        for row in payload["noisy_down_checks"]:
            print(f"    - check {row['check_id']}: {row['count']}")
    else:
        print(
            f"  checks with >= {recent['down_threshold']} down events in last {recent['hours']}h: none"
        )
    print(
        f"  signoff status: {'PASS' if acceptance['all_passed'] else 'FAIL'} "
        f"(mismatches={acceptance['lease_region_mismatch_count']}, "
        f"expired_leases={acceptance['expired_lease_count']}, "
        f"max_expired={acceptance['max_expired_leases_allowed']})"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--recent-hours", type=int, default=1, help="event window in hours")
    p.add_argument("--down-threshold", type=int, default=3, help="down-event threshold for highlighting")
    p.add_argument("--max-expired-leases", type=int, default=0, help="acceptance threshold for expired leases")
    p.add_argument("--environment", type=str, default=os.environ.get("ENV_NAME", "staging"), help="environment label for signoff output")
    p.add_argument("--json-out", type=Path, default=None, help="optional path for machine-readable report")
    p.add_argument("--markdown-out", type=Path, default=None, help="optional path for signoff markdown")
    p.add_argument("--strict", action="store_true", help="exit non-zero when acceptance checks fail")
    args = p.parse_args()

    payload = _build_payload(
        now=_now(),
        recent_hours=max(args.recent_hours, 1),
        down_threshold=max(args.down_threshold, 1),
        max_expired_leases=max(args.max_expired_leases, 0),
        environment=args.environment.strip() or "staging",
    )

    _print_console(payload)

    if args.json_out:
        _write_json(args.json_out, payload)
    if args.markdown_out:
        _write_markdown(args.markdown_out, payload)

    if args.strict and not payload["acceptance"]["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

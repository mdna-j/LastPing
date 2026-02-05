#!/usr/bin/env python3
"""Validate multi-region leases and failover behavior.

This script is intended for local docker-compose validation. It creates (or uses)
an existing check, waits for a lease to appear, and can optionally stop the
primary region worker to observe failover.

Examples:
  python scripts/validate_multi_region.py --region us-east
  python scripts/validate_multi_region.py --region us-east --simulate-failover --stop-primary
  python scripts/validate_multi_region.py --check-id 12 --simulate-failover --stop-primary --start-primary
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from sqlmodel import Session

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.db import engine, create_db_and_tables
from src.models import Project, Check, CheckType, CheckStatus, CheckLease

REGION_SERVICE_MAP = {
    "us-east": "worker_us",
    "eu-west": "worker_eu",
    "ap-south": "worker_ap",
}


def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True)


def _now() -> datetime:
    return datetime.utcnow()


def _wait_for_lease(check_id: int, timeout_s: int) -> CheckLease | None:
    start = time.time()
    while time.time() - start < timeout_s:
        with Session(engine) as session:
            lease = session.get(CheckLease, check_id)
            if lease and lease.lease_owner:
                return lease
        time.sleep(2)
    return None


def _create_project_and_check(region: str, interval: int) -> tuple[int, int]:
    create_db_and_tables()
    with Session(engine) as session:
        project = Project(name=f"multi-region-{int(time.time())}")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name=f"mr-check-{region}",
            type=CheckType.HEARTBEAT,
            region=region,
            expected_interval=interval,
            grace_period=max(1, interval // 5),
            last_ping=_now() - timedelta(seconds=(interval * 4)),
            status=CheckStatus.UP,
            alert_enabled=False,
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        return project.id, check.id


def _get_check(check_id: int) -> Check:
    with Session(engine) as session:
        chk = session.get(Check, check_id)
        if not chk:
            raise SystemExit(f"Check {check_id} not found")
        return chk


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", type=int, default=None)
    p.add_argument("--check-id", type=int, default=None)
    p.add_argument("--region", type=str, default="us-east")
    p.add_argument("--interval", type=int, default=30, help="heartbeat interval seconds")
    p.add_argument("--timeout", type=int, default=600, help="seconds to wait for lease/failover")
    p.add_argument("--simulate-failover", action="store_true")
    p.add_argument("--stop-primary", action="store_true")
    p.add_argument("--start-primary", action="store_true")
    args = p.parse_args()

    if args.check_id is None:
        proj_id, check_id = _create_project_and_check(args.region, args.interval)
        print(f"Created project {proj_id} check {check_id} region={args.region}")
    else:
        chk = _get_check(args.check_id)
        proj_id, check_id = chk.project_id, chk.id
        print(f"Using existing check {check_id} (project {proj_id}, region={chk.region})")

    lease = _wait_for_lease(check_id, args.timeout)
    if not lease:
        raise SystemExit("Timed out waiting for initial lease acquisition.")

    print(f"Lease owner: {lease.lease_owner} expires_at={lease.lease_expires_at}")

    if not args.simulate_failover:
        return

    primary_service = REGION_SERVICE_MAP.get(args.region)
    if args.stop_primary:
        if not primary_service:
            print(f"No compose service mapping for region {args.region}; skip stop.")
        else:
            print(f"Stopping primary worker {primary_service}...")
            _compose("stop", primary_service)

    start_owner = lease.lease_owner
    print("Waiting for failover (lease owner change)...")
    start = time.time()
    while time.time() - start < args.timeout:
        with Session(engine) as session:
            cur = session.get(CheckLease, check_id)
            if cur and cur.lease_owner and cur.lease_owner != start_owner:
                print(f"Failover observed. New lease owner: {cur.lease_owner}")
                break
        time.sleep(5)
    else:
        print("No failover observed within timeout. Check worker logs and lease timings.")

    if args.start_primary:
        if primary_service:
            print(f"Starting primary worker {primary_service}...")
            _compose("start", primary_service)


if __name__ == "__main__":
    main()

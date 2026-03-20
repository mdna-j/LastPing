import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Profile LastPing hot paths locally.")
    p.add_argument("--checks", type=int, default=60, help="Number of synthetic checks to seed")
    p.add_argument("--events-per-check", type=int, default=40, help="Synthetic events per check")
    p.add_argument("--repeat", type=int, default=5, help="Repetitions per profiled scenario")
    p.add_argument(
        "--output-dir",
        default="artifacts/profiling",
        help="Directory for .prof files and summaries",
    )
    p.add_argument(
        "--db-path",
        default="artifacts/profiling/profile.sqlite",
        help="SQLite database path for the profiling run",
    )
    return p.parse_args()


def profile_call(label: str, repeat: int, fn, output_dir: Path) -> dict:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    for _ in range(repeat):
        fn()
    profiler.disable()
    elapsed = time.perf_counter() - started

    prof_path = output_dir / f"{label}.prof"
    txt_path = output_dir / f"{label}.txt"
    profiler.dump_stats(str(prof_path))

    stats_buffer = io.StringIO()
    stats = pstats.Stats(profiler, stream=stats_buffer).sort_stats("cumulative")
    stats.print_stats(25)
    txt_path.write_text(stats_buffer.getvalue(), encoding="utf-8")

    return {
        "label": label,
        "repeat": repeat,
        "elapsed_seconds": round(elapsed, 4),
        "avg_ms": round((elapsed / max(repeat, 1)) * 1000.0, 2),
        "profile": str(prof_path),
        "top_functions": str(txt_path),
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["PUBLIC_RATE_LIMIT_PER_MINUTE"] = "100000"
    os.environ["PUBLIC_USER_RATE_LIMIT_PER_MINUTE"] = "100000"
    os.environ["USER_RATE_LIMIT_PER_MINUTE"] = "100000"
    os.environ["API_RATE_LIMIT_WINDOW_SECONDS"] = "60"

    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.routers.analytics import _compute_failure_trends
    from src.routers.metrics import _compute_availability_report
    from src.models import (
        Check,
        CheckLease,
        CheckResult,
        CheckStatus,
        CheckType,
        Event,
        EventType,
        Incident,
        Project,
        UptimeSnapshot,
    )
    from src.security import hash_api_key
    from src import worker

    dbmod.create_db_and_tables()

    api_key = "profile-key"
    now = datetime.utcnow()

    with Session(dbmod.engine) as session:
        project = Project(
            name="profile-project",
            api_key_hash=hash_api_key(api_key),
            slo_target=99.9,
            sla_target=99.5,
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        checks = []
        check_types = [CheckType.HTTP, CheckType.HEARTBEAT, CheckType.TCP, CheckType.DNS]
        for idx in range(args.checks):
            ctype = check_types[idx % len(check_types)]
            status = CheckStatus.DOWN if idx % 7 == 0 else CheckStatus.UP
            region = "us-east-1" if idx % 2 == 0 else "us-west-2"
            check = Check(
                project_id=project.id,
                name=f"profile-check-{idx}",
                type=ctype,
                status=status,
                url="https://example.com/health" if ctype == CheckType.HTTP else None,
                host="example.com" if ctype in (CheckType.TCP, CheckType.DNS) else None,
                port=443 if ctype == CheckType.TCP else None,
                dns_record_type="A" if ctype == CheckType.DNS else None,
                expected_interval=60 if ctype == CheckType.HEARTBEAT else None,
                grace_period=10 if ctype == CheckType.HEARTBEAT else None,
                last_ping=now - timedelta(minutes=15) if ctype == CheckType.HEARTBEAT and status == CheckStatus.DOWN else now - timedelta(seconds=30),
                latency_threshold_ms=150 if ctype == CheckType.HTTP else None,
                region=region,
                alert_enabled=False,
            )
            session.add(check)
            checks.append(check)
        session.commit()
        for check in checks:
            session.refresh(check)

        incidents = []
        for idx, check in enumerate(checks):
            if idx % 7 == 0:
                inc = Incident(
                    project_id=project.id,
                    check_id=check.id,
                    started_at=now - timedelta(minutes=idx + 3),
                    status="open",
                )
                session.add(inc)
                incidents.append(inc)
        session.commit()
        for inc in incidents:
            session.refresh(inc)

        incident_by_check = {inc.check_id: inc for inc in incidents}

        event_rows = []
        result_rows = []
        snapshot_rows = []
        for idx, check in enumerate(checks):
            for offset in range(args.events_per_check):
                ts = now - timedelta(hours=args.events_per_check - offset)
                event_type = EventType.DOWN if offset % 9 == 0 else EventType.UP
                if offset % 13 == 0:
                    event_type = EventType.HTTP_FAILURE
                event_rows.append(
                    Event(
                        check_id=check.id,
                        project_id=project.id,
                        incident_id=(incident_by_check.get(check.id).id if check.id in incident_by_check and event_type in (EventType.DOWN, EventType.HTTP_FAILURE) else None),
                        event_type=event_type,
                        message=f"profile-event-{idx}-{offset}",
                        created_at=ts,
                    )
                )
                result_rows.append(
                    CheckResult(
                        check_id=check.id,
                        project_id=project.id,
                        incident_id=incident_by_check.get(check.id).id if check.id in incident_by_check else None,
                        status=CheckStatus.DOWN if event_type in (EventType.DOWN, EventType.HTTP_FAILURE) else CheckStatus.UP,
                        latency_ms=45.0 + (offset % 20),
                        error_message="profile-error" if event_type in (EventType.DOWN, EventType.HTTP_FAILURE) else None,
                        created_at=ts,
                    )
                )
            for days_back in range(7):
                window_end = now - timedelta(days=days_back)
                snapshot_rows.append(
                    UptimeSnapshot(
                        project_id=project.id,
                        check_id=check.id,
                        window_start=window_end - timedelta(hours=24),
                        window_end=window_end,
                        uptime_percent=99.0 - (idx % 5),
                        mttr_seconds=120.0 + idx,
                        incidents=1 if idx % 7 == 0 else 0,
                    )
                )
            session.add(
                CheckLease(
                    check_id=check.id,
                    lease_owner=f"worker-{check.region}",
                    lease_expires_at=now + timedelta(minutes=5),
                    updated_at=now,
                    lease_fence=1,
                )
            )
        session.add_all(event_rows)
        session.add_all(result_rows)
        session.add_all(snapshot_rows)
        session.commit()

        project_id = project.id

    worker.notify_down = lambda *args, **kwargs: None
    worker.notify_recovery = lambda *args, **kwargs: None
    worker.notify_degraded = lambda *args, **kwargs: None
    worker._http_check = lambda url, timeout, retries: (True, "status=200", 42.0)
    worker._tcp_check = lambda host, port, timeout: (True, "tcp_ok", 11.0)
    worker._dns_check = lambda host, record_type=None: (True, "dns_ok", 7.0)
    worker._script_check = lambda check, project, timeout=5, retries=1: (True, "script_ok", 15.0)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {api_key}"}
    start = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
    end = now.replace(microsecond=0).isoformat()

    def worker_scan_once():
        with Session(dbmod.engine) as session:
            worker.scan_checks_once(session)

    def dashboard_health():
        response = client.get(f"/ui/dashboard/health?project_id={project_id}")
        response.raise_for_status()

    def incidents_list():
        response = client.get(f"/projects/{project_id}/incidents", headers=headers)
        response.raise_for_status()

    def availability_report():
        response = client.get(
            f"/projects/{project_id}/metrics/availability?start={start}&end={end}",
            headers=headers,
        )
        response.raise_for_status()

    def availability_report_direct():
        with Session(dbmod.engine) as session:
            payload = _compute_availability_report(
                session,
                project_id,
                datetime.fromisoformat(start),
                datetime.fromisoformat(end),
            )
            if payload.get("project_id") != project_id:
                raise RuntimeError("Unexpected project id from direct availability profile")

    def analytics_trends():
        response = client.get(
            f"/projects/{project_id}/analytics/trends?days=30&interval=day",
            headers=headers,
        )
        response.raise_for_status()

    def analytics_trends_direct():
        with Session(dbmod.engine) as session:
            end_dt = datetime.utcnow()
            start_dt = end_dt - timedelta(days=30)
            payload = _compute_failure_trends(
                session,
                project_id,
                start_dt,
                end_dt,
                "day",
            )
            if payload.get("project_id") != project_id:
                raise RuntimeError("Unexpected project id from direct trends profile")

    scenarios = [
        ("worker_scan_once", worker_scan_once),
        ("ui_dashboard_health", dashboard_health),
        ("incidents_list", incidents_list),
        ("metrics_availability", availability_report),
        ("metrics_availability_direct", availability_report_direct),
        ("analytics_trends", analytics_trends),
        ("analytics_trends_direct", analytics_trends_direct),
    ]

    results = [profile_call(label, args.repeat, fn, output_dir) for label, fn in scenarios]
    results.sort(key=lambda row: row["avg_ms"], reverse=True)
    result_map = {row["label"]: row for row in results}

    comparisons = []
    for http_label, direct_label in (
        ("metrics_availability", "metrics_availability_direct"),
        ("analytics_trends", "analytics_trends_direct"),
    ):
        http_row = result_map.get(http_label)
        direct_row = result_map.get(direct_label)
        if not http_row or not direct_row:
            continue
        comparisons.append(
            {
                "http_label": http_label,
                "direct_label": direct_label,
                "http_avg_ms": http_row["avg_ms"],
                "direct_avg_ms": direct_row["avg_ms"],
                "http_overhead_ms": round(http_row["avg_ms"] - direct_row["avg_ms"], 2),
            }
        )

    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "database": str(db_path),
        "checks_seeded": args.checks,
        "events_per_check": args.events_per_check,
        "repeat": args.repeat,
        "results": results,
        "comparisons": comparisons,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Profiling Baseline",
        "",
        f"- checks_seeded: `{args.checks}`",
        f"- events_per_check: `{args.events_per_check}`",
        f"- repeat: `{args.repeat}`",
        "",
        "| Scenario | Avg ms | Total s |",
        "|---|---:|---:|",
    ]
    for row in results:
        lines.append(f"| `{row['label']}` | `{row['avg_ms']}` | `{row['elapsed_seconds']}` |")
    if comparisons:
        lines.extend(
            [
                "",
                "## Direct vs HTTP",
                "",
                "| HTTP scenario | Direct scenario | HTTP avg ms | Direct avg ms | HTTP overhead ms |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in comparisons:
            lines.append(
                f"| `{row['http_label']}` | `{row['direct_label']}` | `{row['http_avg_ms']}` | `{row['direct_avg_ms']}` | `{row['http_overhead_ms']}` |"
            )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

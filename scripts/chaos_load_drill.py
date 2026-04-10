#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import requests

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.chaos_drill import compute_latency_summary, overall_status, utcnow_iso, write_summary_files


def _compose_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _compose_popen(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _json_or_text(response: requests.Response) -> dict:
    try:
        value = response.json()
        if isinstance(value, dict):
            return value
        return {"value": value}
    except Exception:
        return {"text": response.text[:500]}


class LastPingChaosClient:
    def __init__(self, *, base_url: str, admin_token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 15.0,
        headers: Optional[dict[str, str]] = None,
        json_body: Optional[dict] = None,
    ) -> requests.Response:
        merged_headers = dict(headers or {})
        if self.admin_token and "X-ADMIN-TOKEN" not in merged_headers:
            merged_headers["X-ADMIN-TOKEN"] = self.admin_token
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=merged_headers,
            json=json_body,
            timeout=timeout,
        )
        return response

    def create_project(self, name: str) -> dict:
        response = self._request("POST", "/projects/", json_body={"name": name})
        if response.status_code != 200:
            raise RuntimeError(f"Create project failed: {response.status_code} {_json_or_text(response)}")
        payload = response.json()
        if not payload.get("api_key"):
            raise RuntimeError(f"Create project did not return api_key: {payload}")
        return payload

    def create_check(self, project_id: int, api_key: str, payload: dict) -> dict:
        response = self._request(
            "POST",
            f"/projects/{project_id}/checks/",
            headers={"X-API-KEY": api_key},
            json_body=payload,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Create check failed: {response.status_code} {_json_or_text(response)}")
        return response.json()

    def update_project_webhooks(self, project_id: int, api_key: str, payload: dict) -> dict:
        response = self._request(
            "POST",
            f"/projects/{project_id}/webhooks",
            headers={"X-API-KEY": api_key},
            json_body=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Update webhooks failed: {response.status_code} {_json_or_text(response)}")
        return response.json()

    def dashboard_health(self, project_id: int, *, timeout: float = 15.0) -> dict:
        response = self._request("GET", f"/ui/dashboard/health?project_id={project_id}", timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"Dashboard health failed: {response.status_code} {_json_or_text(response)}")
        return response.json()

    def public_status_data(self, project_id: int, *, timeout: float = 10.0) -> requests.Response:
        return self._request("GET", f"/ui/status/{project_id}/data", timeout=timeout, headers={})

    def notification_failures(self, project_id: int, api_key: str) -> list[dict]:
        response = self._request(
            "GET",
            f"/projects/{project_id}/notification-failures",
            headers={"X-API-KEY": api_key},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Notification failures failed: {response.status_code} {_json_or_text(response)}")
        payload = response.json()
        return payload if isinstance(payload, list) else []


def _wait_for(
    predicate: Callable[[], Optional[dict]],
    *,
    timeout_s: int,
    interval_s: float = 2.0,
) -> tuple[bool, Optional[dict]]:
    deadline = time.time() + timeout_s
    last_result: Optional[dict] = None
    while time.time() < deadline:
        try:
            last_result = predicate()
            if last_result is not None:
                return True, last_result
        except Exception as exc:
            last_result = {"error": str(exc)}
        time.sleep(interval_s)
    return False, last_result


def _run_load_burst(
    client: LastPingChaosClient,
    *,
    project_id: int,
    total_requests: int,
    concurrency: int,
) -> dict:
    def _single_request() -> dict:
        started = time.perf_counter()
        response = client.public_status_data(project_id, timeout=15.0)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"status": response.status_code, "elapsed_ms": elapsed_ms}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = [executor.submit(_single_request) for _ in range(total_requests)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    latency_values = [row["elapsed_ms"] for row in results]
    status_counts: dict[str, int] = {}
    for row in results:
        code = str(row["status"])
        status_counts[code] = status_counts.get(code, 0) + 1
    success = status_counts.get("200", 0) == total_requests
    return {
        "status": "ok" if success else "failed",
        "latency_ms": compute_latency_summary(latency_values),
        "status_counts": status_counts,
        "observed": f"{status_counts.get('200', 0)}/{total_requests} succeeded",
    }


def _seed_http_checks(
    client: LastPingChaosClient,
    *,
    project_id: int,
    api_key: str,
    count: int,
    prefix: str,
    url: str,
    interval: int,
    timeout: int,
    alert_enabled: bool,
    alert_webhook_enabled: Optional[bool] = None,
) -> list[dict]:
    rows = []
    for index in range(count):
        payload = {
            "name": f"{prefix}-{index + 1}",
            "type": "http",
            "url": url,
            "interval": interval,
            "timeout": timeout,
            "retries": 1,
            "alert_enabled": alert_enabled,
            "alert_after": 1,
            "alert_cooldown": 0,
        }
        if alert_webhook_enabled is not None:
            payload["alert_webhook_enabled"] = alert_webhook_enabled
        rows.append(client.create_check(project_id, api_key, payload))
    return rows


def _scenario_db_slowness(client: LastPingChaosClient, *, project_id: int, lock_seconds: int) -> dict:
    lock_proc = _compose_popen(
        "exec",
        "-T",
        "db",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "lastping",
        "-d",
        "lastping",
        "-c",
        f'BEGIN; LOCK TABLE "check" IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep({int(lock_seconds)}); COMMIT;',
    )
    time.sleep(1.0)
    started = time.perf_counter()
    response_status = None
    error = None
    try:
        client.dashboard_health(project_id, timeout=float(lock_seconds + 15))
        response_status = 200
    except Exception as exc:
        error = str(exc)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    stdout, stderr = lock_proc.communicate(timeout=lock_seconds + 20)
    lock_ok = lock_proc.returncode == 0
    success = lock_ok and response_status == 200 and elapsed_ms >= max((lock_seconds - 2) * 1000, 1000)
    return {
        "status": "ok" if success else "failed",
        "latency_ms": compute_latency_summary([elapsed_ms]),
        "lock_seconds": lock_seconds,
        "observed": f"dashboard health latency {elapsed_ms}ms",
        "note": "PostgreSQL table lock on check table to simulate DB contention/slowness.",
        "db_lock_stdout": stdout.strip()[-500:],
        "db_lock_stderr": stderr.strip()[-500:],
        "error": error,
    }


def _scenario_worker_failure(
    client: LastPingChaosClient,
    *,
    project_id: int,
    worker_service: str,
    timeout_s: int,
) -> dict:
    _compose_run("stop", worker_service)
    try:
        degraded, degraded_health = _wait_for(
            lambda: (
                health
                if (
                    (health := client.dashboard_health(project_id)).get("platform", {}).get("worker_lag", {}).get("overdue_checks", 0) >= 3
                    and health.get("platform", {}).get("worker_lag", {}).get("state") in {"warning", "critical"}
                )
                else None
            ),
            timeout_s=timeout_s,
            interval_s=3.0,
        )
    finally:
        _compose_run("up", "-d", worker_service)
    recovered, recovered_health = _wait_for(
        lambda: (
            health
            if (
                (health := client.dashboard_health(project_id)).get("platform", {}).get("worker_lag", {}).get("overdue_checks", 0) == 0
                and health.get("platform", {}).get("worker_lag", {}).get("state") in {"healthy", "neutral"}
            )
            else None
        ),
        timeout_s=timeout_s,
        interval_s=3.0,
    )
    success = degraded and recovered
    degraded_state = (degraded_health or {}).get("platform", {}).get("worker_lag", {})
    recovered_state = (recovered_health or {}).get("platform", {}).get("worker_lag", {})
    return {
        "status": "ok" if success else "failed",
        "observed": f"degraded overdue_checks={degraded_state.get('overdue_checks')} recovered overdue_checks={recovered_state.get('overdue_checks')}",
        "degraded_state": degraded_state,
        "recovered_state": recovered_state,
        "note": "Stops the worker container, waits for worker lag to trip, then restarts and verifies recovery.",
    }


def _scenario_redis_loss(
    client: LastPingChaosClient,
    *,
    project_id: int,
    redis_service: str,
    total_requests: int,
) -> dict:
    _compose_run("stop", redis_service)
    status_counts: dict[str, int] = {}
    latency_values: list[float] = []
    try:
        for _ in range(total_requests):
            started = time.perf_counter()
            response = client.public_status_data(project_id, timeout=10.0)
            latency_values.append(round((time.perf_counter() - started) * 1000, 2))
            code = str(response.status_code)
            status_counts[code] = status_counts.get(code, 0) + 1
    finally:
        _compose_run("up", "-d", redis_service)
    success = status_counts.get("429", 0) > 0 and status_counts.get("500", 0) == 0
    return {
        "status": "ok" if success else "failed",
        "latency_ms": compute_latency_summary(latency_values),
        "status_counts": status_counts,
        "observed": f"429 count={status_counts.get('429', 0)} after redis stop",
        "note": "Verifies public-status rate limiting still degrades safely when Redis is unavailable.",
    }


def _scenario_alert_storm_and_integration_outage(
    client: LastPingChaosClient,
    *,
    project_id: int,
    api_key: str,
    checks: int,
    timeout_s: int,
) -> tuple[dict, dict]:
    client.update_project_webhooks(
        project_id,
        api_key,
        {"generic_webhook_url": "http://127.0.0.1:9/chaos-alerts"},
    )
    _seed_http_checks(
        client,
        project_id=project_id,
        api_key=api_key,
        count=checks,
        prefix="chaos-fail",
        url="http://127.0.0.1:9/health",
        interval=5,
        timeout=2,
        alert_enabled=True,
        alert_webhook_enabled=True,
    )

    storm_ok, storm_health = _wait_for(
        lambda: (
            health
            if (
                (health := client.dashboard_health(project_id)).get("down_checks_count", 0) >= min(3, checks)
                and health.get("active_incidents", 0) >= 1
            )
            else None
        ),
        timeout_s=timeout_s,
        interval_s=3.0,
    )
    failure_ok, failures = _wait_for(
        lambda: (
            rows
            if (
                (rows := client.notification_failures(project_id, api_key))
                and any(row.get("channel") == "webhook" and row.get("retryable") for row in rows)
            )
            else None
        ),
        timeout_s=timeout_s,
        interval_s=3.0,
    )

    storm_result = {
        "status": "ok" if storm_ok else "failed",
        "observed": (
            f"down_checks={storm_health.get('down_checks_count')} active_incidents={storm_health.get('active_incidents')}"
            if storm_health
            else "No sustained down-check storm observed"
        ),
        "health": storm_health,
        "note": "Creates multiple failing HTTP checks to force incident and alert fan-out.",
    }
    rows = failures or []
    retryable = sum(1 for row in rows if row.get("retryable"))
    integration_result = {
        "status": "ok" if failure_ok else "failed",
        "observed": f"notification_failures={len(rows)} retryable={retryable}",
        "failures": rows[:5],
        "note": "Routes alerts to an intentionally dead webhook target and verifies failure capture for retry visibility.",
    }
    return storm_result, integration_result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run LastPing load and chaos drills against a local stack.")
    parser.add_argument("--base-url", default=os.environ.get("CHAOS_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN", "chaos-admin-token"))
    parser.add_argument("--output-dir", default="artifacts/chaos_drill")
    parser.add_argument("--format", choices=["text", "github"], default="text")
    parser.add_argument("--load-requests", type=int, default=40)
    parser.add_argument("--load-concurrency", type=int, default=8)
    parser.add_argument("--public-status-requests", type=int, default=95)
    parser.add_argument("--alert-storm-checks", type=int, default=6)
    parser.add_argument("--scenario-timeout", type=int, default=90)
    parser.add_argument("--db-lock-seconds", type=int, default=8)
    parser.add_argument("--worker-service", default="worker")
    parser.add_argument("--redis-service", default="redis")
    args = parser.parse_args(argv)

    client = LastPingChaosClient(base_url=args.base_url, admin_token=args.admin_token)
    started_at = utcnow_iso()
    try:
        worker_project = client.create_project(f"chaos-worker-{int(time.time())}")
        worker_project_id = int(worker_project["id"])
        worker_api_key = str(worker_project["api_key"])
        _seed_http_checks(
            client,
            project_id=worker_project_id,
            api_key=worker_api_key,
            count=3,
            prefix="chaos-worker-health",
            url="http://api:8000/health",
            interval=5,
            timeout=3,
            alert_enabled=False,
        )

        alert_project = client.create_project(f"chaos-alerts-{int(time.time())}")
        alert_project_id = int(alert_project["id"])
        alert_api_key = str(alert_project["api_key"])

        scenarios: dict[str, dict] = {}
        scenarios["api_load_burst"] = _run_load_burst(
            client,
            project_id=worker_project_id,
            total_requests=max(1, args.load_requests),
            concurrency=max(1, args.load_concurrency),
        )
        scenarios["db_slowness"] = _scenario_db_slowness(
            client,
            project_id=worker_project_id,
            lock_seconds=max(1, args.db_lock_seconds),
        )
        scenarios["worker_failure"] = _scenario_worker_failure(
            client,
            project_id=worker_project_id,
            worker_service=args.worker_service,
            timeout_s=max(20, args.scenario_timeout),
        )
        scenarios["redis_loss"] = _scenario_redis_loss(
            client,
            project_id=worker_project_id,
            redis_service=args.redis_service,
            total_requests=max(1, args.public_status_requests),
        )
        alert_storm, integration_outage = _scenario_alert_storm_and_integration_outage(
            client,
            project_id=alert_project_id,
            api_key=alert_api_key,
            checks=max(1, args.alert_storm_checks),
            timeout_s=max(20, args.scenario_timeout),
        )
        scenarios["alert_storm"] = alert_storm
        scenarios["integration_outage"] = integration_outage

        summary = {
            "status": overall_status(scenarios),
            "started_at_utc": started_at,
            "completed_at_utc": utcnow_iso(),
            "base_url": args.base_url,
            "projects": {
                "worker_project_id": worker_project_id,
                "alert_project_id": alert_project_id,
            },
            "scenarios": scenarios,
        }
        write_summary_files(summary, args.output_dir)
        print(f"Chaos/load drill status: {summary['status']}")
        print(f"Artifacts: {args.output_dir}")
        if args.format == "github":
            if summary["status"] == "ok":
                print("::notice::Chaos/load drill completed successfully.")
            else:
                print("::error::Chaos/load drill reported one or more failed scenarios.")
        return 0 if summary["status"] == "ok" else 1
    finally:
        try:
            _compose_run("up", "-d", args.worker_service, check=False)
        except Exception:
            pass
        try:
            _compose_run("up", "-d", args.redis_service, check=False)
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

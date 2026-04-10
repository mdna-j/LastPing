from src.chaos_drill import build_markdown_summary, compute_latency_summary, overall_status


def test_compute_latency_summary_reports_quantiles():
    summary = compute_latency_summary([10, 20, 30, 40, 50])

    assert summary["count"] == 5
    assert summary["avg_ms"] == 30.0
    assert summary["p50_ms"] == 30.0
    assert summary["p95_ms"] == 48.0
    assert summary["max_ms"] == 50.0


def test_overall_status_fails_if_any_scenario_failed():
    status = overall_status(
        {
            "worker_failure": {"status": "ok"},
            "redis_loss": {"status": "failed"},
        }
    )

    assert status == "failed"


def test_build_markdown_summary_includes_scenarios():
    markdown = build_markdown_summary(
        {
            "status": "failed",
            "base_url": "http://127.0.0.1:8000",
            "started_at_utc": "2026-04-10T00:00:00Z",
            "completed_at_utc": "2026-04-10T00:05:00Z",
            "scenarios": {
                "db_slowness": {
                    "status": "ok",
                    "observed": "dashboard health latency 8123ms",
                    "latency_ms": {"count": 1, "avg_ms": 8123.0, "p95_ms": 8123.0, "max_ms": 8123.0},
                },
                "integration_outage": {
                    "status": "failed",
                    "note": "No webhook failure recorded.",
                },
            },
        }
    )

    assert "Chaos And Load Drill" in markdown
    assert "`db_slowness`: `ok`" in markdown
    assert "dashboard health latency 8123ms" in markdown
    assert "`integration_outage`: `failed`" in markdown

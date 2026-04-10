from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[int(position)], 2)
    weight = position - lower
    return round((ordered[lower] * (1 - weight)) + (ordered[upper] * weight), 2)


def compute_latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    ordered = [float(v) for v in values]
    return {
        "count": len(ordered),
        "avg_ms": round(sum(ordered) / len(ordered), 2),
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "max_ms": round(max(ordered), 2),
    }


def overall_status(scenarios: dict[str, dict]) -> str:
    for scenario in scenarios.values():
        if scenario.get("status") != "ok":
            return "failed"
    return "ok"


def build_markdown_summary(summary: dict) -> str:
    lines = ["## Chaos And Load Drill", ""]
    lines.append(f"- Status: `{summary.get('status', 'unknown')}`")
    lines.append(f"- Base URL: `{summary.get('base_url', 'unknown')}`")
    lines.append(f"- Started: `{summary.get('started_at_utc', 'unknown')}`")
    lines.append(f"- Completed: `{summary.get('completed_at_utc', 'unknown')}`")
    lines.append("")
    lines.append("### Scenarios")
    for name, details in summary.get("scenarios", {}).items():
        lines.append(f"- `{name}`: `{details.get('status', 'unknown')}`")
        note = details.get("note")
        if note:
            lines.append(f"  note: {note}")
        latency = details.get("latency_ms")
        if isinstance(latency, dict) and latency.get("count"):
            lines.append(
                "  latency: avg={avg}ms p95={p95}ms max={max}ms".format(
                    avg=latency.get("avg_ms"),
                    p95=latency.get("p95_ms"),
                    max=latency.get("max_ms"),
                )
            )
        if details.get("observed"):
            lines.append(f"  observed: `{details['observed']}`")
    return "\n".join(lines) + "\n"


def write_summary_files(summary: dict, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "summary.json"
    markdown_path = output_path / "summary.md"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(build_markdown_summary(summary), encoding="utf-8")
    return json_path, markdown_path

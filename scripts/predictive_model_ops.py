#!/usr/bin/env python3
"""Run predictive model lifecycle operations (train + quality monitoring).

Examples:
  py -3.11 scripts/predictive_model_ops.py
  py -3.11 scripts/predictive_model_ops.py --project-id 1 --strict
  py -3.11 scripts/predictive_model_ops.py --disable-drifted --json-out artifacts/predictive_model_ops.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import engine
from src.models import PredictiveModel, Project
from src.predictive_models import (
    MODEL_TYPE_SEASONAL,
    evaluate_predictive_model_quality,
    train_seasonal_hourly_models,
)


def _project_ids(session: Session, requested: List[int]) -> List[int]:
    if requested:
        ids = []
        for pid in requested:
            if not session.get(Project, pid):
                raise SystemExit(f"project not found: {pid}")
            ids.append(pid)
        return sorted(set(ids))
    return sorted(session.exec(select(Project.id)).all())


def _retire_superseded_active_models(
    session: Session,
    project_id: int,
    model_type: str,
) -> int:
    models = session.exec(
        select(PredictiveModel).where(
            PredictiveModel.project_id == project_id,
            PredictiveModel.model_type == model_type,
            PredictiveModel.active == True,
        )
    ).all()
    if not models:
        return 0
    models.sort(
        key=lambda m: (
            m.check_id if m.check_id is not None else -1,
            m.version,
            m.trained_at,
        ),
        reverse=True,
    )
    seen: set[int] = set()
    retired = 0
    for model in models:
        check_key = model.check_id if model.check_id is not None else -1
        if check_key not in seen:
            seen.add(check_key)
            continue
        model.active = False
        session.add(model)
        retired += 1
    if retired:
        session.commit()
    return retired


def _disable_drifted_models(session: Session, quality_rows: List[Any]) -> int:
    disabled = 0
    touched = False
    for row in quality_rows:
        if row.status != "drift":
            continue
        model = session.get(PredictiveModel, row.predictive_model_id)
        if not model or not model.active:
            continue
        model.active = False
        session.add(model)
        disabled += 1
        touched = True
    if touched:
        session.commit()
    return disabled


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", type=int, action="append", help="project id to run (repeatable); default is all projects")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--min-events", type=int, default=10)
    p.add_argument("--eval-hours", type=int, default=48)
    p.add_argument("--min-samples", type=int, default=24)
    p.add_argument("--drift-ratio-threshold", type=float, default=2.0)
    p.add_argument("--mae-threshold", type=float, default=1.0)
    p.add_argument("--model-type", type=str, default=MODEL_TYPE_SEASONAL)
    p.add_argument("--disable-drifted", action="store_true", help="deactivate active models that are currently flagged as drift")
    p.add_argument("--strict", action="store_true", help="exit non-zero when drift is detected")
    p.add_argument("--json-out", type=Path, default=None)
    args = p.parse_args()

    if args.model_type != MODEL_TYPE_SEASONAL:
        raise SystemExit("unsupported model_type")

    summary: Dict[str, Any] = {
        "model_type": args.model_type,
        "days": args.days,
        "min_events": args.min_events,
        "eval_hours": args.eval_hours,
        "min_samples": args.min_samples,
        "drift_ratio_threshold": args.drift_ratio_threshold,
        "mae_threshold": args.mae_threshold,
        "disable_drifted": args.disable_drifted,
        "projects": [],
        "totals": {
            "projects": 0,
            "trained": 0,
            "evaluated": 0,
            "drifted": 0,
            "insufficient_data": 0,
            "ok": 0,
            "retired_superseded": 0,
            "disabled_drifted": 0,
        },
    }

    with Session(engine) as session:
        project_ids = _project_ids(session, args.project_id or [])
        for pid in project_ids:
            trained = train_seasonal_hourly_models(
                session=session,
                project_id=pid,
                days=args.days,
                min_events=args.min_events,
                model_type=args.model_type,
            )
            quality_rows = evaluate_predictive_model_quality(
                session=session,
                project_id=pid,
                hours=args.eval_hours,
                min_samples=args.min_samples,
                drift_ratio_threshold=args.drift_ratio_threshold,
                mae_threshold=args.mae_threshold,
                model_type=args.model_type,
            )
            retired_superseded = _retire_superseded_active_models(
                session=session,
                project_id=pid,
                model_type=args.model_type,
            )
            disabled_drifted = (
                _disable_drifted_models(session, quality_rows) if args.disable_drifted else 0
            )

            drifted = sum(1 for row in quality_rows if row.status == "drift")
            insufficient = sum(1 for row in quality_rows if row.status == "insufficient_data")
            ok = len(quality_rows) - drifted - insufficient

            summary["projects"].append(
                {
                    "project_id": pid,
                    "trained": len(trained),
                    "evaluated": len(quality_rows),
                    "drifted": drifted,
                    "insufficient_data": insufficient,
                    "ok": ok,
                    "retired_superseded": retired_superseded,
                    "disabled_drifted": disabled_drifted,
                    "trained_models": [
                        {
                            "id": model.id,
                            "check_id": model.check_id,
                            "version": model.version,
                            "trained_at": model.trained_at.isoformat(),
                        }
                        for model in trained
                    ],
                }
            )

            summary["totals"]["projects"] += 1
            summary["totals"]["trained"] += len(trained)
            summary["totals"]["evaluated"] += len(quality_rows)
            summary["totals"]["drifted"] += drifted
            summary["totals"]["insufficient_data"] += insufficient
            summary["totals"]["ok"] += ok
            summary["totals"]["retired_superseded"] += retired_superseded
            summary["totals"]["disabled_drifted"] += disabled_drifted

    print("Predictive model ops summary")
    print(
        "  projects={projects} trained={trained} evaluated={evaluated} drifted={drifted} ok={ok} insufficient={insufficient}".format(
            projects=summary["totals"]["projects"],
            trained=summary["totals"]["trained"],
            evaluated=summary["totals"]["evaluated"],
            drifted=summary["totals"]["drifted"],
            ok=summary["totals"]["ok"],
            insufficient=summary["totals"]["insufficient_data"],
        )
    )
    print(
        "  retired_superseded={retired} disabled_drifted={disabled}".format(
            retired=summary["totals"]["retired_superseded"],
            disabled=summary["totals"]["disabled_drifted"],
        )
    )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  wrote {args.json_out}")

    if args.strict and summary["totals"]["drifted"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

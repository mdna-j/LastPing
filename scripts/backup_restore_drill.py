#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.backup_restore import run_restore_drill


def _print_github(summary: dict[str, object]) -> None:
    if summary["status"] != "ok":
        print("::error::Backup restore drill failed.")
        if summary.get("mismatched_tables"):
            tables = ", ".join(summary["mismatched_tables"].keys())
            print(f"::error::Mismatched verification tables: {tables}")
        migration = summary.get("migration") or {}
        if migration and not migration.get("ok"):
            print(f"::error::Alembic upgrade failed: {(migration.get('stderr') or migration.get('stdout') or 'unknown')[:300]}")
    else:
        print("::notice::Backup restore drill completed successfully.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a LastPing backup verification and restore drill.")
    parser.add_argument("--source-url", default=os.environ.get("DATABASE_URL"), help="Source database URL.")
    parser.add_argument("--restore-url", required=True, help="Restore target database URL.")
    parser.add_argument("--output-dir", default="artifacts/backup_restore_drill", help="Artifact output directory.")
    parser.add_argument("--keep-backup", action="store_true", help="Keep the backup dump file in the artifact directory.")
    parser.add_argument("--skip-migration-check", action="store_true", help="Skip alembic upgrade head after restore.")
    parser.add_argument("--format", choices=["text", "github"], default="text")
    args = parser.parse_args(argv)

    if not args.source_url:
        print("Missing --source-url (or DATABASE_URL).", file=sys.stderr)
        return 1

    summary = run_restore_drill(
        args.source_url,
        args.restore_url,
        output_dir=Path(args.output_dir),
        keep_backup=args.keep_backup,
        verify_migrations=not args.skip_migration_check,
    )

    print(f"Backup restore drill status: {summary['status']}")
    print(f"Artifact directory: {args.output_dir}")
    print(f"Verification tables: {len(summary['verification_tables'])}")
    if args.format == "github":
        _print_github(summary)
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

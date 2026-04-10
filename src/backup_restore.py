from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine

from .models import (
    Anomaly,
    AuditLog,
    BrowserCheckArtifact,
    Check,
    CheckLease,
    CheckResult,
    Event,
    Incident,
    Project,
    StatusSubscription,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_DATABASE_KINDS = {"sqlite", "postgres"}
DEFAULT_VERIFICATION_TABLE_NAMES = tuple(
    model.__table__.name
    for model in (
        Project,
        Check,
        Event,
        CheckResult,
        Incident,
        AuditLog,
        StatusSubscription,
        Anomaly,
        BrowserCheckArtifact,
        CheckLease,
    )
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_kind(database_url: str) -> str:
    scheme = make_url(database_url).drivername
    if scheme.startswith("sqlite"):
        return "sqlite"
    if scheme.startswith("postgresql"):
        return "postgres"
    raise ValueError(f"Unsupported database URL scheme: {scheme}")


def redact_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def sqlite_path_from_url(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise ValueError("Not a sqlite database URL.")
    if not url.database or url.database == ":memory:":
        raise ValueError("Restore drills do not support in-memory sqlite databases.")
    raw = url.database
    if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_existing_tables(database_url: str) -> list[str]:
    engine = create_engine(database_url, echo=False)
    try:
        inspector = sa.inspect(engine)
        return sorted(inspector.get_table_names())
    finally:
        engine.dispose()


def collect_table_counts(
    database_url: str,
    include_tables: Iterable[str] | None = None,
) -> dict[str, int]:
    selected = set(include_tables or [])
    engine = create_engine(database_url, echo=False)
    try:
        inspector = sa.inspect(engine)
        available = sorted(inspector.get_table_names())
        tables = [name for name in available if not selected or name in selected]
        counts: dict[str, int] = {}
        with engine.connect() as conn:
            for name in tables:
                count = conn.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                counts[name] = int(count or 0)
        return counts
    finally:
        engine.dispose()


def available_verification_tables(database_url: str) -> list[str]:
    existing = set(list_existing_tables(database_url))
    return sorted(name for name in DEFAULT_VERIFICATION_TABLE_NAMES if name in existing)


def _run_command(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _backup_sqlite(source_url: str, dump_path: Path) -> None:
    source_path = sqlite_path_from_url(source_url)
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite source database does not exist: {source_path}")
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dump_path)


def _restore_sqlite(dump_path: Path, restore_url: str) -> None:
    restore_path = sqlite_path_from_url(restore_url)
    restore_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dump_path, restore_path)


def _backup_postgres(source_url: str, dump_path: Path) -> None:
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    completed = _run_command(
        [
            "pg_dump",
            "--format=custom",
            "--file",
            str(dump_path),
            source_url,
        ]
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {completed.stderr.strip() or completed.stdout.strip()}")


def _restore_postgres(dump_path: Path, restore_url: str) -> None:
    completed = _run_command(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--dbname",
            restore_url,
            str(dump_path),
        ]
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {completed.stderr.strip() or completed.stdout.strip()}")


def run_alembic_upgrade(database_url: str) -> dict[str, object]:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    completed = _run_command([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def current_alembic_version(database_url: str) -> str | None:
    engine = create_engine(database_url, echo=False)
    try:
        inspector = sa.inspect(engine)
        if "alembic_version" not in inspector.get_table_names():
            return None
        with engine.connect() as conn:
            value = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if value is None:
                return None
            return str(value)
    finally:
        engine.dispose()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _build_markdown_summary(summary: dict[str, object]) -> str:
    lines = ["## Backup Restore Drill", ""]
    lines.append(f"- Status: `{summary['status']}`")
    lines.append(f"- Source DB: `{summary['source_database_url']}`")
    lines.append(f"- Restore DB: `{summary['restore_database_url']}`")
    lines.append(f"- Database kind: `{summary['database_kind']}`")
    lines.append(f"- Verification tables: `{len(summary['verification_tables'])}`")
    lines.append(f"- Source schema tables: `{summary['source_schema_table_count']}`")
    lines.append(f"- Restored schema tables: `{summary['restored_schema_table_count']}`")
    lines.append(f"- Backup size bytes: `{summary['backup_size_bytes']}`")
    lines.append(f"- Backup sha256: `{summary['backup_sha256']}`")
    lines.append(f"- Backup elapsed: `{summary['backup_elapsed_seconds']}`s")
    lines.append(f"- Restore elapsed: `{summary['restore_elapsed_seconds']}`s")
    if summary.get("migration_elapsed_seconds") is not None:
        lines.append(f"- Migration elapsed: `{summary['migration_elapsed_seconds']}`s")
    lines.append(f"- Alembic head after restore: `{summary.get('alembic_version_after') or 'unknown'}`")
    if summary.get("restored_only_tables"):
        lines.append(f"- Restored-only tables: `{', '.join(summary['restored_only_tables'])}`")
    if summary.get("mismatched_tables"):
        lines.append("")
        lines.append("### Count Mismatches")
        for table_name, mismatch in summary["mismatched_tables"].items():
            lines.append(
                f"- `{table_name}` source=`{mismatch['source_count']}` restored=`{mismatch['restored_count']}`"
            )
    else:
        lines.append("- Count verification: `matched`")
    if summary.get("migration"):
        migration = summary["migration"]
        lines.append(f"- Migration status: `{'ok' if migration['ok'] else 'failed'}`")
        if migration.get("stderr"):
            lines.append(f"- Migration stderr: `{migration['stderr'][:300]}`")
    return "\n".join(lines) + "\n"


def write_summary_files(summary: dict[str, object], output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "summary.json"
    markdown_path = output_path / "summary.md"
    _write_json(json_path, summary)
    markdown_path.write_text(_build_markdown_summary(summary), encoding="utf-8")
    return json_path, markdown_path


def run_restore_drill(
    source_url: str,
    restore_url: str,
    *,
    output_dir: str | Path,
    keep_backup: bool = False,
    verify_migrations: bool = True,
) -> dict[str, object]:
    source_kind = database_kind(source_url)
    restore_kind = database_kind(restore_url)
    if source_kind != restore_kind:
        raise ValueError(
            f"Source and restore database kinds must match for restore drills. "
            f"Got {source_kind} -> {restore_kind}."
        )
    if source_kind not in SUPPORTED_DATABASE_KINDS:
        raise ValueError(f"Unsupported restore drill database kind: {source_kind}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    verification_tables = available_verification_tables(source_url)
    source_schema_tables = list_existing_tables(source_url)
    started_at = utcnow_iso()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    extension = "sqlite3" if source_kind == "sqlite" else "dump"
    backup_path = output_path / f"lastping-backup-restore-drill-{timestamp}.{extension}"

    table_counts_before = collect_table_counts(source_url, verification_tables)

    backup_started = time.perf_counter()
    if source_kind == "sqlite":
        _backup_sqlite(source_url, backup_path)
    else:
        _backup_postgres(source_url, backup_path)
    backup_elapsed = round(time.perf_counter() - backup_started, 3)

    restore_started = time.perf_counter()
    if restore_kind == "sqlite":
        _restore_sqlite(backup_path, restore_url)
    else:
        _restore_postgres(backup_path, restore_url)
    restore_elapsed = round(time.perf_counter() - restore_started, 3)

    migration = None
    migration_elapsed = None
    if verify_migrations:
        migration_started = time.perf_counter()
        migration = run_alembic_upgrade(restore_url)
        migration_elapsed = round(time.perf_counter() - migration_started, 3)

    restored_schema_tables = list_existing_tables(restore_url)
    table_counts_after = collect_table_counts(restore_url, verification_tables)
    mismatched_tables: dict[str, dict[str, int | None]] = {}
    for table_name in verification_tables:
        before = table_counts_before.get(table_name)
        after = table_counts_after.get(table_name)
        if before != after:
            mismatched_tables[table_name] = {
                "source_count": before,
                "restored_count": after,
            }

    restored_only_tables = sorted(set(restored_schema_tables) - set(source_schema_tables))
    success = not mismatched_tables and (migration is None or bool(migration["ok"]))
    summary: dict[str, object] = {
        "status": "ok" if success else "failed",
        "started_at_utc": started_at,
        "completed_at_utc": utcnow_iso(),
        "database_kind": source_kind,
        "source_database_url": redact_database_url(source_url),
        "restore_database_url": redact_database_url(restore_url),
        "verification_tables": verification_tables,
        "source_schema_table_count": len(source_schema_tables),
        "restored_schema_table_count": len(restored_schema_tables),
        "restored_only_tables": restored_only_tables,
        "table_counts_before": table_counts_before,
        "table_counts_after": table_counts_after,
        "mismatched_tables": mismatched_tables,
        "backup_file_name": backup_path.name,
        "backup_kept": keep_backup,
        "backup_path": str(backup_path) if keep_backup else None,
        "backup_size_bytes": backup_path.stat().st_size,
        "backup_sha256": sha256_file(backup_path),
        "backup_elapsed_seconds": backup_elapsed,
        "restore_elapsed_seconds": restore_elapsed,
        "migration_elapsed_seconds": migration_elapsed,
        "migration": migration,
        "alembic_version_after": current_alembic_version(restore_url),
    }

    if not keep_backup and backup_path.exists():
        backup_path.unlink()

    write_summary_files(summary, output_path)
    return summary

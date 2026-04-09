from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse


PROFILE_ALIASES = {
    "prod": "production",
    "production": "production",
    "staging": "staging",
}

COMMON_REQUIRED = ("DATABASE_URL", "BASE_URL", "ADMIN_TOKEN")
PROFILE_REQUIRED = {
    "staging": ("LASTPING_ENCRYPTION_KEY",),
    "production": ("REDIS_URL", "LASTPING_ENCRYPTION_KEY"),
}
PROFILE_RECOMMENDED = {
    "staging": ("REDIS_URL",),
    "production": ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL"),
}
TWILIO_GROUP = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")


@dataclass
class ValidationResult:
    profile: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize_profile(profile: str) -> str:
    normalized = PROFILE_ALIASES.get((profile or "").strip().lower())
    if not normalized:
        raise ValueError(f"Unsupported profile: {profile}")
    return normalized


def parse_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    raw = Path(path).read_text(encoding="utf-8")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _looks_like_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _looks_like_redis_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"redis", "rediss"} and bool(parsed.netloc)


def _looks_like_database_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {
        "postgresql",
        "postgresql+psycopg2",
        "postgresql+asyncpg",
        "sqlite",
        "sqlite+pysqlite",
    }


def _is_sqlite_url(value: str) -> bool:
    return value.startswith("sqlite:")


def validate_env_mapping(env: dict[str, str], profile: str) -> ValidationResult:
    normalized_profile = normalize_profile(profile)
    result = ValidationResult(profile=normalized_profile)
    result.checked.extend(COMMON_REQUIRED)
    result.checked.extend(PROFILE_REQUIRED[normalized_profile])

    for key in COMMON_REQUIRED + PROFILE_REQUIRED[normalized_profile]:
        if not (env.get(key) or "").strip():
            result.errors.append(f"{key} is required for {normalized_profile}.")

    for key in PROFILE_RECOMMENDED[normalized_profile]:
        if not (env.get(key) or "").strip():
            result.warnings.append(f"{key} is recommended for {normalized_profile}.")

    database_url = (env.get("DATABASE_URL") or "").strip()
    if database_url:
        if not _looks_like_database_url(database_url):
            result.errors.append("DATABASE_URL is not a recognized SQLAlchemy-style database URL.")
        if normalized_profile in {"staging", "production"} and _is_sqlite_url(database_url):
            result.errors.append(f"DATABASE_URL must not use sqlite for {normalized_profile}.")

    base_url = (env.get("BASE_URL") or "").strip()
    if base_url:
        if not _looks_like_http_url(base_url):
            result.errors.append("BASE_URL must be a valid http(s) URL.")
        else:
            parsed = urlparse(base_url)
            if normalized_profile == "production" and parsed.scheme != "https":
                result.errors.append("BASE_URL must use https in production.")
            if parsed.hostname in {"localhost", "127.0.0.1"}:
                result.warnings.append("BASE_URL points at localhost; use the public hostname for deployed environments.")

    redis_url = (env.get("REDIS_URL") or "").strip()
    if redis_url and not _looks_like_redis_url(redis_url):
        result.errors.append("REDIS_URL must be a valid redis:// or rediss:// URL.")

    admin_token = (env.get("ADMIN_TOKEN") or "").strip()
    if admin_token and len(admin_token) < 16:
        result.warnings.append("ADMIN_TOKEN is shorter than 16 characters; use a stronger secret.")

    twilio_values = {key: (env.get(key) or "").strip() for key in TWILIO_GROUP}
    if any(twilio_values.values()) and not all(twilio_values.values()):
        missing = ", ".join(key for key, value in twilio_values.items() if not value)
        result.errors.append(f"Twilio SMS config is partial; missing: {missing}.")

    for webhook_key in ("SLACK_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"):
        value = (env.get(webhook_key) or "").strip()
        if value and not _looks_like_http_url(value):
            result.errors.append(f"{webhook_key} must be a valid http(s) URL.")

    script_executor = (env.get("SCRIPT_CHECK_EXECUTOR") or "").strip().lower()
    if normalized_profile in {"staging", "production"} and script_executor in {"host", "local"}:
        result.warnings.append(
            f"SCRIPT_CHECK_EXECUTOR={script_executor} disables isolated script execution in {normalized_profile}; "
            "prefer docker for script checks."
        )

    return result


def _print_text(result: ValidationResult) -> None:
    print(f"Profile: {result.profile}")
    if result.errors:
        print("Errors:")
        for item in result.errors:
            print(f"- {item}")
    if result.warnings:
        print("Warnings:")
        for item in result.warnings:
            print(f"- {item}")
    if result.ok:
        print("Validation passed.")


def _print_github(result: ValidationResult) -> None:
    for item in result.errors:
        print(f"::error::{item}")
    for item in result.warnings:
        print(f"::warning::{item}")
    if result.ok:
        print(f"::notice::Environment validation passed for {result.profile}.")


def _build_env_mapping(env_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if env_file:
        env.update(parse_env_file(env_file))
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate LastPing deployment environment variables.")
    parser.add_argument("--profile", choices=["staging", "production", "prod"], default="staging")
    parser.add_argument("--env-file", default=None, help="Optional env file to validate.")
    parser.add_argument("--format", choices=["text", "json", "github"], default="text")
    args = parser.parse_args(argv)

    result = validate_env_mapping(_build_env_mapping(args.env_file), args.profile)

    if args.format == "json":
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    elif args.format == "github":
        _print_github(result)
    else:
        _print_text(result)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

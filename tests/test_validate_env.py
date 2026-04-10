from scripts.validate_env import parse_env_file, validate_env_mapping


def test_validate_env_staging_accepts_postgres_without_redis(tmp_path):
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://lastping:secret@db.internal:5432/lastping",
                "BASE_URL=https://staging.lastping.example.com",
                "ADMIN_TOKEN=super-secret-admin-token",
                "LASTPING_ENCRYPTION_KEY=staging-encryption-key",
            ]
        ),
        encoding="utf-8",
    )

    env = parse_env_file(env_file)
    result = validate_env_mapping(env, "staging")

    assert result.ok
    assert any("REDIS_URL is recommended" in warning for warning in result.warnings)


def test_validate_env_production_requires_redis_and_https():
    result = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
            "LASTPING_ENCRYPTION_KEY": "prod-encryption-key",
        },
        "production",
    )

    assert not result.ok
    assert any("REDIS_URL is required" in error for error in result.errors)


def test_validate_env_rejects_sqlite_and_http_in_production():
    result = validate_env_mapping(
        {
            "DATABASE_URL": "sqlite:///./dev.db",
            "BASE_URL": "http://lastping.example.com",
            "ADMIN_TOKEN": "short-token",
            "REDIS_URL": "redis://cache.internal:6379/0",
            "LASTPING_ENCRYPTION_KEY": "prod-encryption-key",
        },
        "production",
    )

    assert not result.ok
    assert any("must not use sqlite" in error for error in result.errors)
    assert any("must use https" in error for error in result.errors)
    assert any("shorter than 16 characters" in warning for warning in result.warnings)


def test_validate_env_requires_complete_twilio_group():
    result = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://staging.lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
            "LASTPING_ENCRYPTION_KEY": "staging-encryption-key",
            "TWILIO_ACCOUNT_SID": "sid",
        },
        "staging",
    )

    assert not result.ok
    assert any("Twilio SMS config is partial" in error for error in result.errors)


def test_validate_env_warns_when_host_script_executor_is_used_in_production():
    result = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
            "REDIS_URL": "redis://cache.internal:6379/0",
            "LASTPING_ENCRYPTION_KEY": "prod-encryption-key",
            "SCRIPT_CHECK_EXECUTOR": "host",
        },
        "production",
    )

    assert result.ok
    assert any("disables isolated script execution" in warning for warning in result.warnings)


def test_validate_env_requires_encryption_key_in_staging_and_production():
    staging = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://staging.lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
        },
        "staging",
    )
    production = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
            "REDIS_URL": "redis://cache.internal:6379/0",
        },
        "production",
    )

    assert not staging.ok
    assert any("LASTPING_ENCRYPTION_KEY is required" in error for error in staging.errors)
    assert not production.ok
    assert any("LASTPING_ENCRYPTION_KEY is required" in error for error in production.errors)


def test_validate_env_accepts_otlp_http_export_and_recommends_service_name():
    result = validate_env_mapping(
        {
            "DATABASE_URL": "postgresql://lastping:secret@db.internal:5432/lastping",
            "BASE_URL": "https://staging.lastping.example.com",
            "ADMIN_TOKEN": "super-secret-admin-token",
            "LASTPING_ENCRYPTION_KEY": "staging-encryption-key",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.internal:4318",
        },
        "staging",
    )

    assert result.ok
    assert any("OTEL_SERVICE_NAME is recommended" in warning for warning in result.warnings)

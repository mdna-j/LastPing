from scripts.validate_env import parse_env_file, validate_env_mapping


def test_validate_env_staging_accepts_postgres_without_redis(tmp_path):
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://lastping:secret@db.internal:5432/lastping",
                "BASE_URL=https://staging.lastping.example.com",
                "ADMIN_TOKEN=super-secret-admin-token",
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
            "TWILIO_ACCOUNT_SID": "sid",
        },
        "staging",
    )

    assert not result.ok
    assert any("Twilio SMS config is partial" in error for error in result.errors)

# Application configuration loaded from eneviornment variables.

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest


class Settings(BaseSettings):
    # Configuration values required by LastPing
    database_url: str
    test_database_url: str

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    # Load and cache the application settings.
    return Settings()

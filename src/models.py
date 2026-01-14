"""
Data models for LastPing.

This module defines the persistent domain objects used throughout the
application: `Project`, `Check`, `Heartbeat` and `Event`. Keep schema
fields small and explicit — these models are the source of truth for
Alembic migrations and worker behaviour.
"""

from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # hashed API key for improved security (PBKDF2)
    api_key_hash: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # owner contact for notifications and key rotation delivery
    # This is used to email rotated API keys and escalation notices.
    owner_email: Optional[str] = None

    # per-project webhook configuration (overrides global env vars)
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    pagerduty_integration_key: Optional[str] = None
    generic_webhook_url: Optional[str] = None

    # Relationship to checks owned by this project (one-to-many)
    checks: List["Check"] = Relationship(back_populates="project")
    # per-project alert throttling/escalation
    alert_rate_limit_count: int = Field(default=100, description="max alerts in window before escalation/suppression")
    alert_rate_limit_window: int = Field(default=3600, description="window in seconds for rate limiting alerts")
    last_escalated_at: Optional[datetime] = None


class CheckType(str):
    HEARTBEAT = "heartbeat"
    HTTP = "http"


class CheckStatus(str):
    UP = "UP"
    DOWN = "DOWN"


class Check(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str
    type: str = Field(default=CheckType.HEARTBEAT)

    # heartbeat-specific
    expected_interval: Optional[int] = Field(default=600, description="expected heartbeat interval in seconds")
    grace_period: Optional[int] = Field(default=600, description="additional grace seconds")

    # alerting controls
    alert_enabled: bool = Field(default=True, description="whether alerts are enabled for this check")
    alert_after: int = Field(default=1, description="number of consecutive failures before alerting")
    alert_cooldown: int = Field(default=3600, description="seconds to wait between alerts for this check")
    last_alerted_at: Optional[datetime] = None
    last_alert_type: Optional[str] = None

    # optional maintenance window (suppress alerts during this period)
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None

    # http-specific
    url: Optional[str] = None
    timeout: Optional[int] = Field(default=5)
    retries: Optional[int] = Field(default=1)
    # scheduling for HTTP checks
    interval: Optional[int] = Field(default=60, description="interval in seconds for HTTP checks")
    next_run: Optional[datetime] = None

    status: str = Field(default=CheckStatus.UP)
    last_ping: Optional[datetime] = None
    consecutive_failures: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # reverse relationship to the owning `Project`
    project: Optional[Project] = Relationship(back_populates="checks")
    heartbeats: List["Heartbeat"] = Relationship(back_populates="check")
    events: List["Event"] = Relationship(back_populates="check")


class Heartbeat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="check.id")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Optional[str] = None

    check: Optional[Check] = Relationship(back_populates="heartbeats")


class EventType(str):
    DOWN = "down"
    UP = "up"
    HEARTBEAT = "heartbeat"
    HTTP_FAILURE = "http_failure"


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="check.id")
    project_id: int = Field(foreign_key="project.id")
    event_type: str
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    check: Optional[Check] = Relationship(back_populates="events")

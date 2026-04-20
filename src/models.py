"""
Data models for LastPing.

This module defines the persistent domain objects used throughout the
application: `Project`, `Check`, `Heartbeat` and `Event`. Keep schema
fields small and explicit — these models are the source of truth for
Alembic migrations and worker behaviour.
"""

from datetime import datetime
from typing import List, Optional
from enum import Enum

from pydantic import root_validator
from sqlmodel import Field, Relationship, SQLModel
import sqlalchemy as sa

from .security import EncryptedString, fingerprint_token, hash_api_key


def _encrypted_field(*, description: Optional[str] = None):
    return Field(
        default=None,
        description=description,
        sa_column=sa.Column(EncryptedString(), nullable=True),
    )


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    org_id: Optional[int] = Field(default=None, foreign_key="organization.id", index=True)
    # hashed API key for improved security (PBKDF2)
    api_key_hash: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # owner contact for notifications and key rotation delivery
    # This is used to email rotated API keys and escalation notices.
    owner_email: Optional[str] = None

    # per-project webhook configuration (overrides global env vars)
    discord_webhook_url: Optional[str] = _encrypted_field()
    slack_webhook_url: Optional[str] = _encrypted_field()
    slack_channel: Optional[str] = None
    pagerduty_integration_key: Optional[str] = _encrypted_field()
    generic_webhook_url: Optional[str] = _encrypted_field()
    jira_base_url: Optional[str] = None
    jira_user_email: Optional[str] = _encrypted_field()
    jira_api_token: Optional[str] = _encrypted_field()
    jira_project_key: Optional[str] = None
    jira_issue_type: Optional[str] = Field(default="Task")

    # Relationship to checks owned by this project (one-to-many)
    checks: List["Check"] = Relationship(back_populates="project")
    # API keys issued for this project
    api_keys: List["ApiKey"] = Relationship(back_populates="project")
    # Project membership (users and roles)
    memberships: List["ProjectMembership"] = Relationship(back_populates="project")
    organization: Optional["Organization"] = Relationship(back_populates="projects")
    team_access: List["ProjectTeamAccess"] = Relationship(back_populates="project")
    # per-project alert throttling/escalation
    alert_rate_limit_count: int = Field(default=100, description="max alerts in window before escalation/suppression")
    alert_rate_limit_window: int = Field(default=3600, description="window in seconds for rate limiting alerts")
    last_escalated_at: Optional[datetime] = None
    # default SLO/SLA targets for reporting
    slo_target: Optional[float] = Field(default=99.9, description="target uptime percentage for SLO reporting")
    sla_target: Optional[float] = Field(default=99.5, description="target uptime percentage for SLA reporting")
    # on-call and SMS alert settings (optional; when None fall back to env defaults)
    sms_enabled: Optional[bool] = Field(default=None, description="enable SMS alerts for this project")
    sms_to: Optional[str] = _encrypted_field(description="destination phone number for SMS alerts")
    sms_from: Optional[str] = _encrypted_field(description="override SMS from number (Twilio)")
    sms_provider: Optional[str] = Field(default=None, description="SMS provider id, e.g. 'twilio'")
    sms_account_sid: Optional[str] = _encrypted_field(description="SMS provider account SID (Twilio)")
    sms_auth_token: Optional[str] = _encrypted_field(description="SMS provider auth token (Twilio)")
    oncall_enabled: Optional[bool] = Field(default=None, description="enable on-call email alerts for this project")
    oncall_email: Optional[str] = _encrypted_field(description="destination email address for on-call alerts")
    # optional project-level maintenance window (suppress alerts across project)
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None


class CheckType(str):
    HEARTBEAT = "heartbeat"
    HTTP = "http"
    TCP = "tcp"
    DNS = "dns"
    SCRIPT = "script"
    BROWSER = "browser"


class CheckStatus(str):
    UP = "UP"
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"


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
    # per-channel enablement (None = inherit project/default)
    alert_sms_enabled: Optional[bool] = None
    alert_oncall_enabled: Optional[bool] = None
    alert_slack_enabled: Optional[bool] = None
    alert_discord_enabled: Optional[bool] = None
    alert_pagerduty_enabled: Optional[bool] = None
    alert_webhook_enabled: Optional[bool] = None
    # per-channel routing overrides
    alert_sms_to: Optional[str] = _encrypted_field()
    alert_oncall_email: Optional[str] = _encrypted_field()
    alert_slack_webhook_url: Optional[str] = _encrypted_field()
    alert_slack_channel: Optional[str] = None
    alert_discord_webhook_url: Optional[str] = _encrypted_field()
    alert_pagerduty_integration_key: Optional[str] = _encrypted_field()
    alert_generic_webhook_url: Optional[str] = _encrypted_field()
    # per-check escalation policy (optional)
    escalation_after_minutes: Optional[int] = Field(default=None, description="minutes down before escalating")
    escalation_cooldown_seconds: Optional[int] = Field(default=3600, description="seconds between escalation notifications")
    last_escalated_at: Optional[datetime] = None

    # optional maintenance window (suppress alerts during this period)
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None

    # http-specific
    url: Optional[str] = None
    timeout: Optional[int] = Field(default=5)
    retries: Optional[int] = Field(default=1)
    # tcp/dns shared
    host: Optional[str] = None
    port: Optional[int] = None
    dns_record_type: Optional[str] = None
    # script-based checks (advanced)
    # NOTE: `script_args` stores a JSON-encoded list of args (List[str]).
    # We store JSON to keep the DB schema simple and keep the API ergonomic.
    script_path: Optional[str] = Field(
        default=None,
        description="relative path under CUSTOM_CHECKS_DIR to execute for script checks",
    )
    script_args: Optional[str] = Field(
        default=None,
        description="JSON list of args for script checks",
    )
    browser_steps: Optional[str] = Field(
        default=None,
        description="JSON list of Playwright browser automation steps",
    )
    browser_capture_screenshot: bool = Field(
        default=True,
        description="capture a screenshot on browser check failure",
    )
    # scheduling for HTTP checks
    interval: Optional[int] = Field(default=60, description="interval in seconds for HTTP checks")
    next_run: Optional[datetime] = None
    # latency tracking
    latency_threshold_ms: Optional[int] = Field(default=None, description="latency threshold in ms for degraded state")
    last_latency_ms: Optional[float] = None
    region: Optional[str] = Field(default=None, description="optional region label for distributed workers (comma-separated list or '*' for any)")

    status: str = Field(default=CheckStatus.UP)
    last_ping: Optional[datetime] = None
    consecutive_failures: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # reverse relationship to the owning `Project`
    project: Optional[Project] = Relationship(back_populates="checks")
    heartbeats: List["Heartbeat"] = Relationship(back_populates="check")
    events: List["Event"] = Relationship(back_populates="check")
    check_results: List["CheckResult"] = Relationship(back_populates="check")


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_key"
    """API keys scoped to a project with optional rate limits."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    key_hash: str = Field(index=True)
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=240)
    role: str = Field(default="owner", index=True)
    token_type: str = Field(default="project_token", index=True, description="project_token or service_account")
    managed_by_team_id: Optional[int] = Field(default=None, foreign_key="team.id", index=True)
    is_active: bool = Field(default=True, index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    rate_limit_per_minute: Optional[int] = Field(default=0, description="requests per minute allowed for this key; 0 = unlimited")
    last_used_at: Optional[datetime] = Field(default=None, index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    last_rotated_at: datetime = Field(default_factory=datetime.utcnow)
    rotation_interval_days: Optional[int] = Field(default=None, description="recommended forced-rotation interval in days")
    replaced_by_api_key_id: Optional[int] = Field(default=None, foreign_key="api_key.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Optional[Project] = Relationship(back_populates="api_keys")


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TeamRole(str, Enum):
    LEAD = "lead"
    MEMBER = "member"


class Organization(SQLModel, table=True):
    __tablename__ = "organization"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    slug: Optional[str] = Field(default=None, index=True)
    scim_bearer_token: Optional[str] = _encrypted_field(description="org-scoped SCIM bearer token")
    scim_last_rotated_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["OrganizationMembership"] = Relationship(back_populates="organization")
    teams: List["Team"] = Relationship(back_populates="organization")
    projects: List["Project"] = Relationship(back_populates="organization")


class OrganizationMembership(SQLModel, table=True):
    __tablename__ = "organization_membership"
    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = Field(default=OrgRole.MEMBER.value, index=True)
    managed_provider: Optional[str] = Field(default=None, index=True)
    managed_group: Optional[str] = Field(default=None)
    managed_fallback_role: Optional[str] = Field(default=None)
    managed_last_synced_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    organization: Optional["Organization"] = Relationship(back_populates="memberships")
    user: Optional["User"] = Relationship(back_populates="organization_memberships")


class Team(SQLModel, table=True):
    __tablename__ = "team"
    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True)
    name: str = Field(index=True)
    slug: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    organization: Optional["Organization"] = Relationship(back_populates="teams")
    memberships: List["TeamMembership"] = Relationship(back_populates="team")
    project_access: List["ProjectTeamAccess"] = Relationship(back_populates="team")


class TeamMembership(SQLModel, table=True):
    __tablename__ = "team_membership"
    id: Optional[int] = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = Field(default=TeamRole.MEMBER.value, index=True)
    managed_provider: Optional[str] = Field(default=None, index=True)
    managed_group: Optional[str] = Field(default=None)
    managed_fallback_role: Optional[str] = Field(default=None)
    managed_last_synced_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    team: Optional["Team"] = Relationship(back_populates="memberships")
    user: Optional["User"] = Relationship(back_populates="team_memberships")


class OrganizationGroupMapping(SQLModel, table=True):
    __tablename__ = "organization_group_mapping"
    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True)
    provider: str = Field(index=True)
    external_group: str = Field(index=True)
    role: str = Field(default=OrgRole.MEMBER.value, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TeamGroupMapping(SQLModel, table=True):
    __tablename__ = "team_group_mapping"
    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    provider: str = Field(index=True)
    external_group: str = Field(index=True)
    role: str = Field(default=TeamRole.MEMBER.value, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectTeamAccess(SQLModel, table=True):
    __tablename__ = "project_team_access"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    team_id: int = Field(foreign_key="team.id", index=True)
    role: str = Field(default=Role.VIEWER.value, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Optional["Project"] = Relationship(back_populates="team_access")
    team: Optional["Team"] = Relationship(back_populates="project_access")


class User(SQLModel, table=True):
    __tablename__ = "user"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    hashed_password: str
    display_name: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    mfa_secret: Optional[str] = _encrypted_field(description="encrypted TOTP seed for user MFA")
    mfa_enabled_at: Optional[datetime] = Field(default=None)
    last_login_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["ProjectMembership"] = Relationship(back_populates="user")
    organization_memberships: List["OrganizationMembership"] = Relationship(back_populates="user")
    team_memberships: List["TeamMembership"] = Relationship(back_populates="user")
    identities: List["UserIdentity"] = Relationship(back_populates="user")


class ProjectMembership(SQLModel, table=True):
    __tablename__ = "project_membership"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    project_id: int = Field(foreign_key="project.id")
    role: str = Field(default=Role.VIEWER.value)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    user: Optional[User] = Relationship(back_populates="memberships")
    project: Optional[Project] = Relationship(back_populates="memberships")


class UserToken(SQLModel, table=True):
    __tablename__ = "user_token"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    token: str = Field(index=True)
    token_fingerprint: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    last_seen_at: Optional[datetime] = Field(default=None)
    session_name: Optional[str] = Field(default=None)
    auth_method: Optional[str] = Field(default="password", index=True)
    auth_provider: Optional[str] = Field(default=None, index=True)
    issued_from_ip: Optional[str] = Field(default=None)
    issued_user_agent: Optional[str] = Field(default=None, sa_column=sa.Column(sa.Text(), nullable=True))
    mfa_verified_at: Optional[datetime] = Field(default=None)

    user: Optional[User] = Relationship()

    @root_validator(pre=False)
    def _normalize_token_storage(cls, values):
        token = values.get("token")
        token_fingerprint = values.get("token_fingerprint")
        if token and not token_fingerprint and not str(token).startswith("pbkdf2_sha256$"):
            values["token_fingerprint"] = fingerprint_token(token)
            values["token"] = hash_api_key(token)
        return values


class UserIdentity(SQLModel, table=True):
    __tablename__ = "user_identity"
    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_subject", name="uq_user_identity_provider_subject"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(index=True)
    provider_subject: str = Field(index=True)
    email: Optional[str] = Field(default=None, index=True)
    display_name: Optional[str] = Field(default=None)
    last_groups_json: Optional[str] = Field(default=None, sa_column=sa.Column(sa.Text(), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = Field(default=None)

    user: Optional[User] = Relationship(back_populates="identities")


class UptimeSnapshot(SQLModel, table=True):
    __tablename__ = "uptime_snapshot"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    check_id: int = Field(foreign_key="check.id")
    window_start: datetime
    window_end: datetime
    uptime_percent: float
    mttr_seconds: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Anomaly(SQLModel, table=True):
    __tablename__ = "anomaly"
    id: Optional[int] = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="check.id", index=True)
    incident_id: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    type: str = Field(index=True, description="latency_spike, flapping, missed_heartbeat, etc")
    severity: float
    window_start: datetime
    window_end: datetime
    evidence_json: str = Field(default="{}", description="JSON-encoded evidence payload")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AvailabilityRollup(SQLModel, table=True):
    __tablename__ = "availability_rollup"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: Optional[int] = Field(default=None, foreign_key="check.id", index=True)
    period_type: str = Field(default="month", index=True)
    period: str = Field(index=True)
    period_start: datetime
    period_end: datetime
    uptime_percent: float
    slo_met: Optional[bool] = None
    sla_met: Optional[bool] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SLOCompliancePeriod(SQLModel, table=True):
    __tablename__ = "slo_compliance_period"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "check_id", "period_type", "period", name="uq_slo_compliance_period_scope"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: Optional[int] = Field(default=None, foreign_key="check.id", index=True)
    period_type: str = Field(default="day", index=True, description="day, month, or quarter")
    period: str = Field(index=True, description="normalized period label such as 2026-04-19 or 2026-04")
    period_start: datetime = Field(index=True)
    period_end: datetime = Field(index=True)
    slo_target: Optional[float] = Field(default=None)
    sla_target: Optional[float] = Field(default=None)
    uptime_percent: float
    error_budget_percent: Optional[float] = Field(default=None)
    error_rate_percent: Optional[float] = Field(default=None)
    budget_seconds: Optional[float] = Field(default=None)
    consumed_seconds: Optional[float] = Field(default=None)
    remaining_seconds: Optional[float] = Field(default=None)
    consumed_percent: Optional[float] = Field(default=None)
    remaining_percent: Optional[float] = Field(default=None)
    slo_met: Optional[bool] = Field(default=None)
    sla_met: Optional[bool] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class PredictiveModel(SQLModel, table=True):
    __tablename__ = "predictive_model"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: Optional[int] = Field(default=None, foreign_key="check.id", index=True)
    model_type: str = Field(default="seasonal_hourly_v1", index=True)
    version: int = Field(default=1)
    trained_at: datetime = Field(default_factory=datetime.utcnow)
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    params_json: str = Field(default="{}", description="model parameters JSON")
    metrics_json: Optional[str] = None
    active: bool = Field(default=True, index=True)


class PredictiveModelQuality(SQLModel, table=True):
    __tablename__ = "predictive_model_quality"
    id: Optional[int] = Field(default=None, primary_key=True)
    predictive_model_id: int = Field(foreign_key="predictive_model.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: int = Field(foreign_key="check.id", index=True)
    window_start: datetime
    window_end: datetime
    sample_count: int = Field(default=0)
    mae: Optional[float] = None
    rmse: Optional[float] = None
    mape: Optional[float] = None
    drift_ratio: Optional[float] = None
    status: str = Field(default="ok", index=True, description="ok, drift, insufficient_data")
    metrics_json: Optional[str] = Field(default=None, description="JSON-encoded monitoring details")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ProjectSecretLifecycle(SQLModel, table=True):
    __tablename__ = "project_secret_lifecycle"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "secret_name", name="uq_project_secret_lifecycle_project_secret"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    secret_name: str = Field(index=True, description="project secret field name, e.g. jira_api_token")
    last_used_at: Optional[datetime] = Field(default=None, index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    last_rotated_at: Optional[datetime] = Field(default=None)
    rotation_interval_days: Optional[int] = Field(default=None, description="recommended forced-rotation interval in days")
    previous_secret_value: Optional[str] = _encrypted_field(description="previous secret retained during rollover grace")
    previous_secret_expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BrowserCheckSecret(SQLModel, table=True):
    __tablename__ = "browser_check_secret"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "name", name="uq_browser_check_secret_project_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str = Field(index=True, description="project-scoped browser secret name referenced by browser steps")
    value: Optional[str] = _encrypted_field(description="encrypted browser secret value")
    description: Optional[str] = Field(default=None, description="operator-facing description for the secret")
    last_used_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ApiKeyUsage(SQLModel, table=True):
    __tablename__ = "api_key_usage"
    """Simple per-minute counter for API key usage enforcement."""
    id: Optional[int] = Field(default=None, primary_key=True)
    api_key_id: int = Field(foreign_key="api_key.id")
    minute_start: datetime = Field(description="UTC minute window start (seconds=0, micros=0)")
    count: int = Field(default=0)

    # relationship back to ApiKey (not strictly necessary for queries)
    # api_key: Optional[ApiKey] = Relationship()


class UserUsage(SQLModel, table=True):
    __tablename__ = "user_usage"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    minute_start: datetime = Field(description="UTC minute window start (seconds=0, micros=0)")
    count: int = Field(default=0)



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
    DEGRADED = "degraded"


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="check.id")
    project_id: int = Field(foreign_key="project.id")
    event_type: str
    message: Optional[str] = None
    incident_id: Optional[int] = Field(default=None, foreign_key="incident.id")
    run_key: Optional[str] = Field(default=None, index=True, description="idempotency key for a single check execution run")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    check: Optional[Check] = Relationship(back_populates="events")


class CheckResult(SQLModel, table=True):
    __tablename__ = "check_result"
    id: Optional[int] = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="check.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    incident_id: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    run_key: Optional[str] = Field(default=None, index=True, description="idempotency key for a single check execution run")
    status: str = Field(description="observed check status during this execution")
    latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    check: Optional[Check] = Relationship(back_populates="check_results")


class BrowserCheckArtifact(SQLModel, table=True):
    __tablename__ = "browser_check_artifact"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: int = Field(foreign_key="check.id", index=True)
    check_result_id: Optional[int] = Field(default=None, foreign_key="check_result.id", index=True)
    incident_id: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    artifact_type: str = Field(index=True, description="screenshot, video, har, trace, or report")
    file_path: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class Incident(SQLModel, table=True):
    __tablename__ = "incident"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    check_id: int = Field(foreign_key="check.id")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    status: str = Field(default="open")
    share_token: Optional[str] = None
    slack_thread_ts: Optional[str] = Field(default=None, index=True, description="Slack thread timestamp for threaded incident updates")
    slack_channel_id: Optional[str] = Field(default=None, description="Slack channel used for threaded incident updates")
    pagerduty_dedup_key: Optional[str] = Field(default=None, index=True, description="PagerDuty dedup key used to sync incident lifecycle")
    jira_issue_key: Optional[str] = Field(default=None, index=True, description="Jira issue key linked to this incident")
    jira_issue_url: Optional[str] = Field(default=None, description="Browsable Jira issue URL linked to this incident")
    # Optional grouping: incidents that are related can share a `group_id`.
    # Pointing to another incident (the group's representative) allows
    # grouping without creating a separate table. This is used by the
    # worker to combine related failures into a single incident view.
    group_id: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    # If an incident was merged into another, track the target here.
    merged_into: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    owner: Optional[str] = Field(default=None, description="incident owner or responder handle")
    acknowledged_at: Optional[datetime] = Field(default=None, description="when the incident was acknowledged")
    acknowledged_by: Optional[str] = Field(default=None, description="actor who acknowledged the incident")
    resolved_by: Optional[str] = Field(default=None, description="actor who manually resolved the incident")
    resolution_summary: Optional[str] = Field(
        default=None,
        sa_column=sa.Column(sa.Text(), nullable=True),
        description="operator-entered summary describing how the incident was resolved",
    )
    silenced_until: Optional[datetime] = Field(default=None, description="suppress notifications until this time")
    silenced_by: Optional[str] = Field(default=None, description="actor who silenced the incident")
    open_run_key: Optional[str] = Field(default=None, index=True, description="idempotency key for incident creation run")
    resolve_run_key: Optional[str] = Field(default=None, index=True, description="idempotency key for incident resolution run")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # relationships
    # events relationship is available via Event.incident_id


class IncidentNote(SQLModel, table=True):
    __tablename__ = "incident_note"
    id: Optional[int] = Field(default=None, primary_key=True)
    incident_id: int = Field(foreign_key="incident.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    author: Optional[str] = Field(default=None, description="actor who created the note")
    body: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class StatusSubscription(SQLModel, table=True):
    __tablename__ = "status_subscription"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    channel: str = Field(index=True, description="notification channel: email or webhook")
    target: str = Field(index=True, description="email address or webhook URL")
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class NotificationDelivery(SQLModel, table=True):
    __tablename__ = "notification_delivery"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    check_id: Optional[int] = Field(default=None, foreign_key="check.id", index=True)
    incident_id: Optional[int] = Field(default=None, foreign_key="incident.id", index=True)
    subscription_id: Optional[int] = Field(default=None, foreign_key="status_subscription.id", index=True)
    channel: str = Field(index=True, description="delivery channel: slack, pagerduty, jira, email, webhook, discord")
    event: str = Field(index=True, description="logical event name such as down, recovery, jira_ticket, or status_opened")
    request_kind: str = Field(index=True, description="executor kind for this queued delivery")
    target: Optional[str] = Field(default=None, description="safe display target, never a raw secret")
    payload_json: str = Field(default="{}", description="JSON-encoded executor payload")
    status: str = Field(default="queued", index=True, description="queued, retry, processing, delivered, or dead")
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=5)
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    claimed_by: Optional[str] = Field(default=None, index=True)
    claimed_at: Optional[datetime] = Field(default=None, index=True)
    last_error: Optional[str] = None
    last_status_code: Optional[int] = None
    delivered_at: Optional[datetime] = Field(default=None, index=True)
    dead_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    actor: Optional[str] = None
    action: str = Field(description="Action performed, e.g. 'create_apikey', 'rotate_apikey', 'revoke_apikey'")
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    org_id: Optional[int] = Field(default=None, index=True)
    team_id: Optional[int] = Field(default=None, index=True)
    project_id: Optional[int] = Field(default=None, index=True)
    details: Optional[str] = None
    # optional network context for the actor performing the action
    actor_ip: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookReceipt(SQLModel, table=True):
    __tablename__ = "webhook_receipt"
    __table_args__ = (sa.UniqueConstraint("source", "signature", name="uq_webhook_receipt_source_signature"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    signature: str = Field(index=True)
    request_timestamp: datetime = Field(index=True)
    received_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AdminCsrf(SQLModel, table=True):
    __tablename__ = "admin_csrf"
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class CheckLease(SQLModel, table=True):
    __tablename__ = "check_lease"
    check_id: int = Field(primary_key=True, foreign_key="check.id")
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None)
    lease_fence: int = Field(default=0, description="monotonic fencing token incremented on each successful lease acquisition")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RemediationHook(SQLModel, table=True):
    __tablename__ = "remediation_hook"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    check_id: Optional[int] = Field(default=None, foreign_key="check.id")
    event_type: str = Field(description="down or degraded")
    url: str
    method: str = Field(default="POST")
    enabled: bool = Field(default=True)
    cooldown_seconds: int = Field(default=900)
    last_triggered_at: Optional[datetime] = None
    secret: Optional[str] = _encrypted_field()
    require_secret: bool = Field(default=False, description="require a secret to be set before triggering")
    require_approval: bool = Field(default=False, description="require manual approval before triggering")
    max_triggers_per_day: Optional[int] = Field(default=50, description="max remediation triggers per 24h")
    failure_count: int = Field(default=0, description="consecutive failure count")
    disable_on_failure_count: Optional[int] = Field(default=5, description="disable hook after this many failures")
    disabled_at: Optional[datetime] = None
    disabled_reason: Optional[str] = None
    allow_during_maintenance: bool = Field(default=False, description="allow remediation during maintenance windows")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RemediationLog(SQLModel, table=True):
    __tablename__ = "remediation_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    hook_id: int = Field(foreign_key="remediation_hook.id")
    project_id: int = Field(foreign_key="project.id")
    check_id: Optional[int] = Field(default=None, foreign_key="check.id")
    event_type: str
    status: str
    response_code: Optional[int] = None
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RemediationApproval(SQLModel, table=True):
    __tablename__ = "remediation_approval"
    id: Optional[int] = Field(default=None, primary_key=True)
    hook_id: int = Field(foreign_key="remediation_hook.id")
    project_id: int = Field(foreign_key="project.id")
    check_id: int = Field(foreign_key="check.id")
    event_type: str
    reason: Optional[str] = None
    status: str = Field(default="pending", description="pending/approved/denied/expired/executed/failed")
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_status: Optional[str] = None
    execution_message: Optional[str] = None


class EscalationTarget(str, Enum):
    ROTATION = "rotation"
    EMAIL = "email"
    SMS = "sms"


class OnCallRotation(SQLModel, table=True):
    __tablename__ = "oncall_rotation"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str
    interval_minutes: int = Field(default=1440, description="rotation interval in minutes")
    start_at: datetime = Field(default_factory=datetime.utcnow)
    enabled: bool = Field(default=True)


class OnCallMember(SQLModel, table=True):
    __tablename__ = "oncall_member"
    id: Optional[int] = Field(default=None, primary_key=True)
    rotation_id: int = Field(foreign_key="oncall_rotation.id")
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    order: int = Field(default=0)
    active: bool = Field(default=True)


class OnCallEscalation(SQLModel, table=True):
    __tablename__ = "oncall_escalation"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    check_id: Optional[int] = Field(default=None, foreign_key="check.id")
    level: int = Field(default=0, index=True)
    delay_minutes: int = Field(default=15)
    target_type: str = Field(default=EscalationTarget.ROTATION.value)
    rotation_id: Optional[int] = Field(default=None, foreign_key="oncall_rotation.id")
    target_value: Optional[str] = None
    enabled: bool = Field(default=True)
    # Optional comma-separated event filters, e.g. "down" or "down,degraded"
    event_types: Optional[str] = Field(default=None)


class OnCallAlert(SQLModel, table=True):
    __tablename__ = "oncall_alert"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    check_id: int = Field(foreign_key="check.id")
    event_type: str
    message: Optional[str] = None
    status: str = Field(default="open")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_notified_at: Optional[datetime] = None
    escalation_level: int = Field(default=0)
    next_escalation_at: Optional[datetime] = None

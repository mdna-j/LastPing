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

from sqlmodel import Field, Relationship, SQLModel


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
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    slack_channel: Optional[str] = None
    pagerduty_integration_key: Optional[str] = None
    generic_webhook_url: Optional[str] = None
    jira_base_url: Optional[str] = None
    jira_user_email: Optional[str] = None
    jira_api_token: Optional[str] = None
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
    sms_to: Optional[str] = Field(default=None, description="destination phone number for SMS alerts")
    sms_from: Optional[str] = Field(default=None, description="override SMS from number (Twilio)")
    sms_provider: Optional[str] = Field(default=None, description="SMS provider id, e.g. 'twilio'")
    sms_account_sid: Optional[str] = Field(default=None, description="SMS provider account SID (Twilio)")
    sms_auth_token: Optional[str] = Field(default=None, description="SMS provider auth token (Twilio)")
    oncall_enabled: Optional[bool] = Field(default=None, description="enable on-call email alerts for this project")
    oncall_email: Optional[str] = Field(default=None, description="destination email address for on-call alerts")
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
    alert_sms_to: Optional[str] = None
    alert_oncall_email: Optional[str] = None
    alert_slack_webhook_url: Optional[str] = None
    alert_slack_channel: Optional[str] = None
    alert_discord_webhook_url: Optional[str] = None
    alert_pagerduty_integration_key: Optional[str] = None
    alert_generic_webhook_url: Optional[str] = None
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
    role: str = Field(default="owner", index=True)
    is_active: bool = Field(default=True, index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    rate_limit_per_minute: Optional[int] = Field(default=0, description="requests per minute allowed for this key; 0 = unlimited")
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
    created_at: datetime = Field(default_factory=datetime.utcnow)

    team: Optional["Team"] = Relationship(back_populates="memberships")
    user: Optional["User"] = Relationship(back_populates="team_memberships")


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
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["ProjectMembership"] = Relationship(back_populates="user")
    organization_memberships: List["OrganizationMembership"] = Relationship(back_populates="user")
    team_memberships: List["TeamMembership"] = Relationship(back_populates="user")


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    user: Optional[User] = Relationship()


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
    secret: Optional[str] = None
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

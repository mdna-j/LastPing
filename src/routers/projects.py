"""
Project management routes.

Endpoints here create projects and manage per-project webhook
configuration and API keys. `create_project` returns a plaintext API
key once; the server stores only the PBKDF2 hash.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Path, Body
from pydantic import BaseModel, EmailStr, AnyHttpUrl, confloat, constr, root_validator
from sqlmodel import select, Session

from ..db import get_session
from ..models import AuditLog, ApiKey, OrgRole, Project as ProjectModel, ProjectMembership, Role
from ..security import generate_api_key, hash_api_key
from ..secret_lifecycle import (
    SECRET_DISCORD_WEBHOOK_URL,
    SECRET_GENERIC_WEBHOOK_URL,
    SECRET_JIRA_API_TOKEN,
    SECRET_PAGERDUTY_INTEGRATION_KEY,
    SECRET_SLACK_WEBHOOK_URL,
    UNSET,
    active_project_secret_candidates,
    api_key_rotation_due_at,
    api_key_rotation_required,
    clear_project_secret_lifecycle,
    project_secret_lifecycle_payload,
    rotate_project_secret,
    touch_project_secret_last_used,
    update_project_secret_policy,
)
from ..alerts import retry_notification_failure_payload, send_pagerduty_event
from ..deps import authorize_org_operation, authorize_project_operation, get_audit_context, get_current_user, limit_integration_action_requests, limit_public_requests
from ..schemas import StrictBaseModel
import os
import json


router = APIRouter(prefix="/projects", tags=["projects"])


_SECRET_AUDIT_FIELDS = {
    "discord_webhook_url",
    "slack_webhook_url",
    "pagerduty_integration_key",
    "generic_webhook_url",
    "jira_user_email",
    "jira_api_token",
    "sms_to",
    "sms_from",
    "sms_account_sid",
    "sms_auth_token",
    "oncall_email",
}


def _audit_value_for_field(key: str, value):
    if key in _SECRET_AUDIT_FIELDS:
        return "[configured]" if value else None
    return value


def _diff_details(before: dict, after: dict) -> Optional[str]:
    changes = {}
    for key, old_val in before.items():
        old_val = _audit_value_for_field(key, old_val)
        new_val = _audit_value_for_field(key, after.get(key))
        if old_val != new_val:
            changes[key] = {"from": old_val, "to": new_val}
    if not changes:
        return None
    try:
        return json.dumps(changes, default=str)
    except Exception:
        return str(changes)


def _normalize_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class ProjectCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    owner_email: Optional[EmailStr] = None
    org_id: Optional[int] = None


class ProjectRead(BaseModel):
    id: int
    name: str
    org_id: Optional[int] = None
    created_at: datetime
    slo_target: Optional[float] = None
    sla_target: Optional[float] = None
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class ProjectTokenCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    role: constr(regex=r"^(owner|admin|editor|viewer)$") = Role.EDITOR.value
    rate_limit_per_minute: Optional[int] = 0
    expires_at: Optional[datetime] = None
    rotation_interval_days: Optional[int] = None

    @root_validator
    def _validate_lifecycle(cls, values):
        expires_at = _normalize_utc_naive(values.get("expires_at"))
        rotation_interval_days = values.get("rotation_interval_days")
        values["expires_at"] = expires_at
        if expires_at and expires_at <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        if rotation_interval_days is not None and int(rotation_interval_days) <= 0:
            raise ValueError("rotation_interval_days must be positive")
        return values


class ProjectTokenLifecycleUpdate(StrictBaseModel):
    expires_at: Optional[datetime] = None
    clear_expiry: bool = False
    rotation_interval_days: Optional[int] = None
    clear_rotation_policy: bool = False

    @root_validator
    def _validate_lifecycle(cls, values):
        expires_at = _normalize_utc_naive(values.get("expires_at"))
        rotation_interval_days = values.get("rotation_interval_days")
        values["expires_at"] = expires_at
        if expires_at and expires_at <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        if rotation_interval_days is not None and int(rotation_interval_days) <= 0:
            raise ValueError("rotation_interval_days must be positive")
        return values


class ProjectTokenRotateRequest(ProjectTokenLifecycleUpdate):
    grace_seconds: int = 3600

    @root_validator
    def _validate_grace(cls, values):
        grace_seconds = int(values.get("grace_seconds") or 0)
        if grace_seconds < 0 or grace_seconds > 604800:
            raise ValueError("grace_seconds must be between 0 and 604800")
        return values


class ProjectTokenRead(BaseModel):
    id: int
    project_id: int
    name: Optional[str]
    role: str
    is_active: bool
    revoked_at: Optional[datetime]
    rate_limit_per_minute: Optional[int]
    created_by_user_id: Optional[int]
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    last_rotated_at: datetime
    rotation_interval_days: Optional[int]
    rotation_due_at: Optional[datetime]
    rotation_required: bool
    replaced_by_api_key_id: Optional[int]
    is_primary: bool
    created_at: datetime

    class Config:
        orm_mode = True


class SecretLifecycleRead(BaseModel):
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    last_rotated_at: Optional[datetime] = None
    rotation_interval_days: Optional[int] = None
    rotation_due_at: Optional[datetime] = None
    rotation_required: bool = False
    rollover_active_until: Optional[datetime] = None
    usable_now: bool = False


def _token_lifecycle_update_kwargs(payload: ProjectTokenLifecycleUpdate) -> dict:
    expires_at = UNSET
    rotation_interval_days = UNSET
    if payload.clear_expiry:
        expires_at = None
    elif "expires_at" in getattr(payload, "__fields_set__", set()):
        expires_at = _normalize_utc_naive(payload.expires_at)
    if payload.clear_rotation_policy:
        rotation_interval_days = None
    elif "rotation_interval_days" in getattr(payload, "__fields_set__", set()):
        rotation_interval_days = payload.rotation_interval_days
    return {
        "expires_at": expires_at,
        "rotation_interval_days": rotation_interval_days,
    }


def _serialize_project_token(token: ApiKey, project: ProjectModel) -> ProjectTokenRead:
    return ProjectTokenRead(
        id=token.id,
        project_id=token.project_id,
        name=token.name,
        role=token.role,
        is_active=token.is_active,
        revoked_at=token.revoked_at,
        rate_limit_per_minute=token.rate_limit_per_minute,
        created_by_user_id=token.created_by_user_id,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        last_rotated_at=token.last_rotated_at or token.created_at,
        rotation_interval_days=token.rotation_interval_days,
        rotation_due_at=api_key_rotation_due_at(token),
        rotation_required=api_key_rotation_required(token),
        replaced_by_api_key_id=token.replaced_by_api_key_id,
        is_primary=bool(project.api_key_hash and token.key_hash == project.api_key_hash),
        created_at=token.created_at,
    )


def _secret_lifecycle_update_kwargs(payload) -> dict:
    expires_at = UNSET
    rotation_interval_days = UNSET
    if getattr(payload, "clear_expiry", False):
        expires_at = None
    elif "expires_at" in getattr(payload, "__fields_set__", set()):
        expires_at = _normalize_utc_naive(getattr(payload, "expires_at", None))
    if getattr(payload, "clear_rotation_policy", False):
        rotation_interval_days = None
    elif "rotation_interval_days" in getattr(payload, "__fields_set__", set()):
        rotation_interval_days = getattr(payload, "rotation_interval_days", None)
    return {
        "expires_at": expires_at,
        "rotation_interval_days": rotation_interval_days,
    }


def _project_scope_kwargs(project: ProjectModel) -> dict:
    return {
        "project_id": project.id,
        "org_id": getattr(project, "org_id", None),
    }


@router.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_public_requests)])
def create_project(payload: ProjectCreate, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Create a new project and return the project and API key."""
    current_user = None
    if isinstance(authorization, str) and authorization:
        current_user = get_current_user(authorization=authorization, session=session)
    if payload.org_id is not None:
        authorize_org_operation(
            payload.org_id,
            min_role=OrgRole.ADMIN.value,
            x_admin_token=x_admin_token,
            authorization=authorization,
            session=session,
        )

    # generate one-time plaintext API key and store only the hash
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    project = ProjectModel(
        name=payload.name,
        org_id=payload.org_id,
        api_key_hash=api_key_hash,
        owner_email=payload.owner_email,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    if current_user:
        session.add(ProjectMembership(user_id=current_user.id, project_id=project.id, role=Role.OWNER.value))
        session.commit()
    # Ensure the primary project API key is also represented in the `api_key`
    # table so write endpoints guarded by `limit_by_api_key` accept it.
    try:
        session.add(
            ApiKey(
                project_id=project.id,
                key_hash=api_key_hash,
                name="primary",
                role=Role.OWNER.value,
                rate_limit_per_minute=0,
                created_by_user_id=current_user.id if current_user else None,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(
            actor=actor,
            action="create_project",
            target_type="project",
            target_id=project.id,
            details=None,
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_project_scope_kwargs(project),
        )
        session.add(al)
        session.commit()
    except Exception:
        pass
    # Caller must securely persist `api_key` — it will not be shown again.
    return {"project": ProjectRead.from_orm(project), "api_key": api_key}


@router.get("/", response_model=List[ProjectRead], dependencies=[Depends(limit_public_requests)])
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(ProjectModel)).all()
    return [ProjectRead.from_orm(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectRead, dependencies=[Depends(limit_public_requests)])
def get_project(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRead.from_orm(project)


@router.get("/{project_id}/tokens", response_model=List[ProjectTokenRead])
def list_project_tokens(
    project_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    rows = session.exec(select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())).all()
    return [_serialize_project_token(row, project) for row in rows]


@router.post("/{project_id}/tokens", status_code=status.HTTP_201_CREATED)
def create_project_token(
    project_id: int = Path(..., ge=1),
    payload: ProjectTokenCreate = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    creator = None
    if isinstance(authorization, str) and authorization:
        try:
            creator = get_current_user(authorization=authorization, session=session)
        except HTTPException:
            creator = None
    plain = generate_api_key()
    token = ApiKey(
        project_id=project_id,
        key_hash=hash_api_key(plain),
        name=payload.name,
        role=payload.role,
        rate_limit_per_minute=payload.rate_limit_per_minute or 0,
        created_by_user_id=creator.id if creator else None,
        expires_at=payload.expires_at,
        rotation_interval_days=payload.rotation_interval_days,
        last_rotated_at=datetime.utcnow(),
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        session.add(
            AuditLog(
                actor=actor,
                action="create_scoped_project_token",
                target_type="api_key",
                target_id=token.id,
                details=f"name={payload.name}, role={payload.role}",
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return {"token": _serialize_project_token(token, project), "api_key": plain}


@router.post("/{project_id}/tokens/{api_key_id}/revoke", response_model=ProjectTokenRead)
def revoke_project_token(
    project_id: int = Path(..., ge=1),
    api_key_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    token = session.get(ApiKey, api_key_id)
    if not token or token.project_id != project_id:
        raise HTTPException(status_code=404, detail="API token not found")
    if token.key_hash == project.api_key_hash:
        raise HTTPException(status_code=400, detail="Use rotate-key for the primary project token")
    token.is_active = False
    token.revoked_at = datetime.utcnow()
    session.add(token)
    session.commit()
    session.refresh(token)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        session.add(
            AuditLog(
                actor=actor,
                action="revoke_scoped_project_token",
                target_type="api_key",
                target_id=token.id,
                details=f"name={token.name}, role={token.role}",
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return _serialize_project_token(token, project)


@router.post("/{project_id}/tokens/{api_key_id}/policy", response_model=ProjectTokenRead)
def update_project_token_policy(
    project_id: int = Path(..., ge=1),
    api_key_id: int = Path(..., ge=1),
    payload: ProjectTokenLifecycleUpdate = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    token = session.get(ApiKey, api_key_id)
    if not token or token.project_id != project_id:
        raise HTTPException(status_code=404, detail="API token not found")
    update_kwargs = _token_lifecycle_update_kwargs(payload)
    if update_kwargs["expires_at"] is not UNSET:
        token.expires_at = update_kwargs["expires_at"]
    if update_kwargs["rotation_interval_days"] is not UNSET:
        token.rotation_interval_days = update_kwargs["rotation_interval_days"]
    session.add(token)
    session.commit()
    session.refresh(token)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        session.add(
            AuditLog(
                actor=actor,
                action="update_project_token_policy",
                target_type="api_key",
                target_id=token.id,
                details=json.dumps(
                    {
                        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                        "rotation_interval_days": token.rotation_interval_days,
                    }
                ),
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return _serialize_project_token(token, project)


@router.post("/{project_id}/tokens/{api_key_id}/rotate", status_code=status.HTTP_201_CREATED)
def rotate_project_token(
    project_id: int = Path(..., ge=1),
    api_key_id: int = Path(..., ge=1),
    payload: Optional[ProjectTokenRotateRequest] = Body(None),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    token = session.get(ApiKey, api_key_id)
    if not token or token.project_id != project_id:
        raise HTTPException(status_code=404, detail="API token not found")
    if token.key_hash == project.api_key_hash:
        raise HTTPException(status_code=400, detail="Use rotate-key for the primary project token")

    payload = payload or ProjectTokenRotateRequest()
    plain = generate_api_key()
    update_kwargs = _token_lifecycle_update_kwargs(payload)
    new_token = ApiKey(
        project_id=project_id,
        key_hash=hash_api_key(plain),
        name=token.name,
        role=token.role,
        rate_limit_per_minute=token.rate_limit_per_minute or 0,
        created_by_user_id=token.created_by_user_id,
        expires_at=update_kwargs["expires_at"] if update_kwargs["expires_at"] is not UNSET else token.expires_at,
        rotation_interval_days=update_kwargs["rotation_interval_days"] if update_kwargs["rotation_interval_days"] is not UNSET else token.rotation_interval_days,
        last_rotated_at=datetime.utcnow(),
    )
    session.add(new_token)
    session.flush()

    token.expires_at = datetime.utcnow() + timedelta(seconds=int(payload.grace_seconds or 0))
    token.replaced_by_api_key_id = new_token.id
    session.add(token)
    session.commit()
    session.refresh(token)
    session.refresh(new_token)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        session.add(
            AuditLog(
                actor=actor,
                action="rotate_scoped_project_token",
                target_type="api_key",
                target_id=new_token.id,
                details=json.dumps(
                    {
                        "replaced_api_key_id": token.id,
                        "grace_seconds": int(payload.grace_seconds or 0),
                    }
                ),
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return {"token": _serialize_project_token(new_token, project), "api_key": plain}


class WebhookUpdate(StrictBaseModel):
    discord_webhook_url: Optional[AnyHttpUrl] = None
    slack_webhook_url: Optional[AnyHttpUrl] = None
    slack_channel: Optional[constr(max_length=120)] = None
    pagerduty_integration_key: Optional[constr(max_length=128)] = None
    generic_webhook_url: Optional[AnyHttpUrl] = None
    clear_discord_webhook_url: bool = False
    clear_slack_webhook_url: bool = False
    clear_pagerduty_integration_key: bool = False
    clear_generic_webhook_url: bool = False


class WebhookSettingsOut(BaseModel):
    discord_webhook_configured: bool
    slack_webhook_configured: bool
    slack_channel: Optional[str] = None
    pagerduty_integration_key_configured: bool
    generic_webhook_configured: bool


class SloSettings(StrictBaseModel):
    slo_target: Optional[confloat(ge=0, le=100)] = None
    sla_target: Optional[confloat(ge=0, le=100)] = None


class AlertSettings(StrictBaseModel):
    sms_enabled: Optional[bool] = None
    sms_to: Optional[constr(regex=r"^\\+?[0-9]{7,20}$")] = None
    oncall_enabled: Optional[bool] = None
    oncall_email: Optional[EmailStr] = None


class PagerDutySettingsIn(StrictBaseModel):
    integration_key: Optional[constr(max_length=128)] = None
    clear_integration_key: bool = False
    expires_at: Optional[datetime] = None
    clear_expiry: bool = False
    rotation_interval_days: Optional[int] = None
    clear_rotation_policy: bool = False
    grace_seconds: int = 3600

    @root_validator
    def _validate_lifecycle(cls, values):
        expires_at = _normalize_utc_naive(values.get("expires_at"))
        rotation_interval_days = values.get("rotation_interval_days")
        grace_seconds = int(values.get("grace_seconds") or 0)
        values["expires_at"] = expires_at
        if expires_at and expires_at <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        if rotation_interval_days is not None and int(rotation_interval_days) <= 0:
            raise ValueError("rotation_interval_days must be positive")
        if grace_seconds < 0 or grace_seconds > 604800:
            raise ValueError("grace_seconds must be between 0 and 604800")
        return values


class PagerDutySettingsOut(BaseModel):
    integration_key_configured: bool
    secret_lifecycle: SecretLifecycleRead
    inbound_webhook_url: str
    inbound_secret_configured: bool
    inbound_timestamp_header: str
    inbound_signature_header: str
    inbound_signature_scheme: str
    latest_sync_action: Optional[str] = None
    latest_sync_at: Optional[datetime] = None


class PagerDutyTestResult(BaseModel):
    ok: bool
    dedup_key: str
    trigger_sent: bool
    resolve_sent: bool
    message: str


class JiraSettingsIn(StrictBaseModel):
    base_url: Optional[AnyHttpUrl] = None
    user_email: Optional[EmailStr] = None
    api_token: Optional[constr(max_length=255)] = None
    project_key: Optional[constr(max_length=64)] = None
    issue_type: Optional[constr(max_length=120)] = None
    clear_api_token: bool = False
    expires_at: Optional[datetime] = None
    clear_expiry: bool = False
    rotation_interval_days: Optional[int] = None
    clear_rotation_policy: bool = False
    grace_seconds: int = 3600

    @root_validator
    def _validate_lifecycle(cls, values):
        expires_at = _normalize_utc_naive(values.get("expires_at"))
        rotation_interval_days = values.get("rotation_interval_days")
        grace_seconds = int(values.get("grace_seconds") or 0)
        values["expires_at"] = expires_at
        if expires_at and expires_at <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        if rotation_interval_days is not None and int(rotation_interval_days) <= 0:
            raise ValueError("rotation_interval_days must be positive")
        if grace_seconds < 0 or grace_seconds > 604800:
            raise ValueError("grace_seconds must be between 0 and 604800")
        return values


class JiraSettingsOut(BaseModel):
    base_url: Optional[str] = None
    user_email: Optional[str] = None
    api_token_configured: bool
    secret_lifecycle: SecretLifecycleRead
    project_key: Optional[str] = None
    issue_type: Optional[str] = None
    inbound_webhook_url: str
    inbound_secret_configured: bool
    inbound_timestamp_header: str
    inbound_signature_header: str
    inbound_signature_scheme: str
    latest_sync_action: Optional[str] = None
    latest_sync_at: Optional[datetime] = None
    configured: bool


class NotificationFailureOut(BaseModel):
    id: int
    created_at: datetime
    channel: str
    event: str
    detail: Optional[str] = None
    target: Optional[str] = None
    check_id: Optional[int] = None
    subscription_id: Optional[int] = None
    retryable: bool = False
    request_kind: Optional[str] = None
    last_retry_action: Optional[str] = None
    last_retry_at: Optional[datetime] = None


class NotificationRetryResult(BaseModel):
    ok: bool
    message: str
    target: Optional[str] = None
    status: Optional[int] = None


class MaintenanceWindow(StrictBaseModel):
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None

    @root_validator
    def _validate_window(cls, values):
        start = values.get("maintenance_starts_at")
        end = values.get("maintenance_ends_at")
        if start and end and end <= start:
            raise ValueError("maintenance_ends_at must be after maintenance_starts_at")
        return values


def _webhook_settings_out(project: ProjectModel) -> WebhookSettingsOut:
    return WebhookSettingsOut(
        discord_webhook_configured=bool(project.discord_webhook_url),
        slack_webhook_configured=bool(project.slack_webhook_url),
        slack_channel=project.slack_channel,
        pagerduty_integration_key_configured=bool(project.pagerduty_integration_key),
        generic_webhook_configured=bool(project.generic_webhook_url),
    )


@router.get("/{project_id}/webhooks", response_model=WebhookSettingsOut)
def get_project_webhooks(
    project_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    return _webhook_settings_out(project)


@router.post("/{project_id}/webhooks", response_model=WebhookSettingsOut)
def update_project_webhooks(project_id: int = Path(..., ge=1), payload: WebhookUpdate = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), _rl=None, session: Session = Depends(get_session)):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {
        "discord_webhook_url": project.discord_webhook_url,
        "slack_webhook_url": project.slack_webhook_url,
        "slack_channel": project.slack_channel,
        "pagerduty_integration_key": project.pagerduty_integration_key,
        "generic_webhook_url": project.generic_webhook_url,
    }
    fields_set = getattr(payload, "__fields_set__", set())
    if payload.clear_discord_webhook_url:
        project.discord_webhook_url = None
        clear_project_secret_lifecycle(session, project_id, SECRET_DISCORD_WEBHOOK_URL)
    elif payload.discord_webhook_url:
        discord_value = str(payload.discord_webhook_url)
        if discord_value != project.discord_webhook_url:
            rotate_project_secret(session, project, SECRET_DISCORD_WEBHOOK_URL, new_value=discord_value, grace_seconds=0)
    if payload.clear_slack_webhook_url:
        project.slack_webhook_url = None
        clear_project_secret_lifecycle(session, project_id, SECRET_SLACK_WEBHOOK_URL)
    elif payload.slack_webhook_url:
        slack_value = str(payload.slack_webhook_url)
        if slack_value != project.slack_webhook_url:
            rotate_project_secret(session, project, SECRET_SLACK_WEBHOOK_URL, new_value=slack_value, grace_seconds=0)
    if "slack_channel" in fields_set:
        project.slack_channel = payload.slack_channel
    if payload.clear_pagerduty_integration_key:
        project.pagerduty_integration_key = None
        clear_project_secret_lifecycle(session, project_id, SECRET_PAGERDUTY_INTEGRATION_KEY)
    elif payload.pagerduty_integration_key:
        if payload.pagerduty_integration_key != project.pagerduty_integration_key:
            rotate_project_secret(
                session,
                project,
                SECRET_PAGERDUTY_INTEGRATION_KEY,
                new_value=payload.pagerduty_integration_key,
                grace_seconds=0,
            )
    if payload.clear_generic_webhook_url:
        project.generic_webhook_url = None
        clear_project_secret_lifecycle(session, project_id, SECRET_GENERIC_WEBHOOK_URL)
    elif payload.generic_webhook_url:
        generic_value = str(payload.generic_webhook_url)
        if generic_value != project.generic_webhook_url:
            rotate_project_secret(session, project, SECRET_GENERIC_WEBHOOK_URL, new_value=generic_value, grace_seconds=0)
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "discord_webhook_url": project.discord_webhook_url,
            "slack_webhook_url": project.slack_webhook_url,
            "slack_channel": project.slack_channel,
            "pagerduty_integration_key": project.pagerduty_integration_key,
            "generic_webhook_url": project.generic_webhook_url,
        }
        al = AuditLog(actor=actor, action="update_project_webhooks", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(project))
        session.add(al)
        session.commit()
    except Exception:
        pass
    return _webhook_settings_out(project)


@router.get("/{project_id}/maintenance", response_model=MaintenanceWindow, dependencies=[Depends(limit_public_requests)])
def get_project_maintenance(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return MaintenanceWindow(maintenance_starts_at=project.maintenance_starts_at, maintenance_ends_at=project.maintenance_ends_at)


@router.post("/{project_id}/maintenance", response_model=MaintenanceWindow)
def set_project_maintenance(project_id: int = Path(..., ge=1), payload: MaintenanceWindow = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), _rl=None, session: Session = Depends(get_session)):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {
        "maintenance_starts_at": project.maintenance_starts_at,
        "maintenance_ends_at": project.maintenance_ends_at,
    }
    project.maintenance_starts_at = payload.maintenance_starts_at
    project.maintenance_ends_at = payload.maintenance_ends_at
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "maintenance_starts_at": project.maintenance_starts_at,
            "maintenance_ends_at": project.maintenance_ends_at,
        }
        al = AuditLog(actor=actor, action="set_project_maintenance", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(project))
        session.add(al)
        session.commit()
    except Exception:
        pass
    return payload


@router.post("/{project_id}/rotate-key")
def rotate_api_key(
    project_id: int = Path(..., ge=1),
    payload: Optional[ProjectTokenRotateRequest] = Body(None),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    payload = payload or ProjectTokenRotateRequest()
    lifecycle_kwargs = _token_lifecycle_update_kwargs(payload)
    now = datetime.utcnow()
    new_key = generate_api_key()
    old_hash = getattr(project, "api_key_hash", None)
    new_hash = hash_api_key(new_key)
    current_primary = None
    if old_hash:
        current_primary = session.exec(
            select(ApiKey).where(ApiKey.project_id == project_id, ApiKey.key_hash == old_hash).order_by(ApiKey.created_at.desc())
        ).first()

    new_primary = ApiKey(
        project_id=project_id,
        key_hash=new_hash,
        name="primary",
        role=Role.OWNER.value,
        rate_limit_per_minute=(current_primary.rate_limit_per_minute if current_primary else 0) or 0,
        created_by_user_id=current_primary.created_by_user_id if current_primary else None,
        expires_at=lifecycle_kwargs["expires_at"] if lifecycle_kwargs["expires_at"] is not UNSET else None,
        rotation_interval_days=lifecycle_kwargs["rotation_interval_days"] if lifecycle_kwargs["rotation_interval_days"] is not UNSET else (current_primary.rotation_interval_days if current_primary else None),
        last_rotated_at=now,
    )
    session.add(new_primary)
    session.flush()

    grace_until = now + timedelta(seconds=int(payload.grace_seconds or 0))
    if current_primary:
        current_primary.name = "primary-rollover"
        current_primary.expires_at = grace_until
        current_primary.replaced_by_api_key_id = new_primary.id
        session.add(current_primary)
    elif old_hash:
        session.add(
            ApiKey(
                project_id=project_id,
                key_hash=old_hash,
                name="primary-rollover",
                role=Role.OWNER.value,
                rate_limit_per_minute=0,
                expires_at=grace_until,
                last_rotated_at=now,
                replaced_by_api_key_id=new_primary.id,
            )
        )

    project.api_key_hash = new_hash
    # Email the new key to the project owner when configured. This is
    # the safe most practical delivery mechanism for production usage.
    from ..alerts import send_email
    if getattr(project, 'owner_email', None):
        subject = f"[LastPing] API key rotated for project {project.name}"
        body = f"A new API key was generated for project {project.name}:\n\n{new_key}\n\nStore this securely; it will not be shown again."
        try:
            send_email(subject, body, to=project.owner_email)
        except Exception:
            # We swallow email errors here — rotation still succeeds.
            pass
    session.add(project)
    session.commit()
    session.refresh(project)
    session.refresh(new_primary)
    # audit
    try:
        from ..models import AuditLog
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(
            actor=actor or "project_api",
            action="rotate_primary_api_key",
            target_type="project",
            target_id=project_id,
            details=json.dumps({"grace_seconds": int(payload.grace_seconds or 0), "new_api_key_id": new_primary.id}),
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_project_scope_kwargs(project),
        )
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"api_key": new_key, "token": _serialize_project_token(new_primary, project)}


@router.post('/rotate-all-keys')
def rotate_all_keys(request: Request = None, x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Admin endpoint to rotate API keys for all projects.

    Protected by `ADMIN_TOKEN` env var. Supply the admin token in header `X-ADMIN-TOKEN`.
    Returns mapping of project id -> new plaintext key.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token:
        raise HTTPException(status_code=403, detail='Admin endpoint not enabled')
    if x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail='Invalid admin token')

    projects = session.exec(select(ProjectModel)).all()
    result = {}
    for p in projects:
        old_hash = getattr(p, "api_key_hash", None)
        new_key = generate_api_key()
        new_hash = hash_api_key(new_key)
        now = datetime.utcnow()
        current_primary = None
        if old_hash:
            current_primary = session.exec(
                select(ApiKey).where(ApiKey.project_id == p.id, ApiKey.key_hash == old_hash).order_by(ApiKey.created_at.desc())
            ).first()
        new_primary = ApiKey(
            project_id=p.id,
            key_hash=new_hash,
            name="primary",
            role=Role.OWNER.value,
            rate_limit_per_minute=(current_primary.rate_limit_per_minute if current_primary else 0) or 0,
            created_by_user_id=current_primary.created_by_user_id if current_primary else None,
            rotation_interval_days=current_primary.rotation_interval_days if current_primary else None,
            last_rotated_at=now,
        )
        session.add(new_primary)
        session.flush()
        p.api_key_hash = new_hash
        grace_until = now + timedelta(hours=1)
        if current_primary:
            current_primary.name = "primary-rollover"
            current_primary.expires_at = grace_until
            current_primary.replaced_by_api_key_id = new_primary.id
            session.add(current_primary)
        elif old_hash:
            session.add(
                ApiKey(
                    project_id=p.id,
                    key_hash=old_hash,
                    name="primary-rollover",
                    role=Role.OWNER.value,
                    rate_limit_per_minute=0,
                    expires_at=grace_until,
                    last_rotated_at=now,
                    replaced_by_api_key_id=new_primary.id,
                )
            )
        session.add(p)
        result[p.id] = new_key
        # email rotated key to owner when available
        try:
            if getattr(p, 'owner_email', None):
                from ..alerts import send_email
                subj = f"[LastPing] API key rotated for project {p.name}"
                body = f"A new API key was generated for project {p.name}:\n\n{new_key}\n\nStore this securely; it will not be shown again."
                send_email(subj, body, to=p.owner_email)
        except Exception:
            pass
        # audit per project rotation
        try:
            actor, actor_ip, user_agent = get_audit_context(request, None, x_admin_token, session)
            al = AuditLog(actor=actor, action="rotate_project_key_admin", target_type="project", target_id=p.id, details="rotate_all_keys", actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(p))
            session.add(al)
        except Exception:
            pass
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, None, x_admin_token, session)
        al = AuditLog(actor=actor, action="rotate_all_keys", target_type="admin", target_id=None, details=f"count={len(result)}", actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return result


@router.get("/{project_id}/slo", response_model=SloSettings, dependencies=[Depends(limit_public_requests)])
def get_project_slo(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return SloSettings(slo_target=project.slo_target, sla_target=project.sla_target)


@router.post("/{project_id}/slo", response_model=SloSettings)
def set_project_slo(project_id: int = Path(..., ge=1), payload: SloSettings = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {"slo_target": project.slo_target, "sla_target": project.sla_target}
    if payload.slo_target is not None:
        project.slo_target = payload.slo_target
    if payload.sla_target is not None:
        project.sla_target = payload.sla_target
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {"slo_target": project.slo_target, "sla_target": project.sla_target}
        al = AuditLog(actor=actor, action="set_project_slo", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(project))
        session.add(al)
        session.commit()
    except Exception:
        pass
    return SloSettings(slo_target=project.slo_target, sla_target=project.sla_target)


@router.get("/{project_id}/alert-settings", response_model=AlertSettings)
def get_project_alert_settings(
    project_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    return AlertSettings(
        sms_enabled=project.sms_enabled,
        sms_to=project.sms_to,
        oncall_enabled=project.oncall_enabled,
        oncall_email=project.oncall_email,
    )


@router.post("/{project_id}/alert-settings", response_model=AlertSettings)
def set_project_alert_settings(project_id: int = Path(..., ge=1), payload: AlertSettings = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {
        "sms_enabled": project.sms_enabled,
        "sms_to": project.sms_to,
        "oncall_enabled": project.oncall_enabled,
        "oncall_email": project.oncall_email,
    }
    if payload.sms_enabled is not None:
        project.sms_enabled = payload.sms_enabled
    if payload.sms_to is not None:
        project.sms_to = payload.sms_to
    if payload.oncall_enabled is not None:
        project.oncall_enabled = payload.oncall_enabled
    if payload.oncall_email is not None:
        project.oncall_email = payload.oncall_email
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "sms_enabled": project.sms_enabled,
            "sms_to": project.sms_to,
            "oncall_enabled": project.oncall_enabled,
            "oncall_email": project.oncall_email,
        }
        al = AuditLog(actor=actor, action="set_project_alert_settings", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(project))
        session.add(al)
        session.commit()
    except Exception:
        pass
    return AlertSettings(
        sms_enabled=project.sms_enabled,
        sms_to=project.sms_to,
        oncall_enabled=project.oncall_enabled,
        oncall_email=project.oncall_email,
    )


def _pagerduty_settings_out(session: Session, project: ProjectModel) -> PagerDutySettingsOut:
    base_url = (os.environ.get("BASE_URL") or "").rstrip("/")
    inbound_path = "/integrations/pagerduty/webhook"
    inbound_url = f"{base_url}{inbound_path}" if base_url else inbound_path
    latest_sync = session.exec(
        select(AuditLog)
        .where(
            AuditLog.project_id == project.id,
            AuditLog.target_type == "incident",
            AuditLog.action.like("pagerduty_%"),
        )
        .order_by(AuditLog.created_at.desc())
    ).first()
    return PagerDutySettingsOut(
        integration_key_configured=bool(project.pagerduty_integration_key),
        secret_lifecycle=SecretLifecycleRead(**project_secret_lifecycle_payload(session, project, SECRET_PAGERDUTY_INTEGRATION_KEY)),
        inbound_webhook_url=inbound_url,
        inbound_secret_configured=bool(os.environ.get("PAGERDUTY_WEBHOOK_SECRET")),
        inbound_timestamp_header="X-PagerDuty-Webhook-Timestamp",
        inbound_signature_header="X-PagerDuty-Webhook-Signature",
        inbound_signature_scheme="HMAC-SHA256 over '<timestamp>.<raw_body>' using PAGERDUTY_WEBHOOK_SECRET",
        latest_sync_action=latest_sync.action if latest_sync else None,
        latest_sync_at=latest_sync.created_at if latest_sync else None,
    )


def _jira_settings_out(session: Session, project: ProjectModel) -> JiraSettingsOut:
    base_url = (os.environ.get("BASE_URL") or "").rstrip("/")
    inbound_path = "/integrations/jira/webhook"
    inbound_url = f"{base_url}{inbound_path}" if base_url else inbound_path
    latest_sync = session.exec(
        select(AuditLog)
        .where(
            AuditLog.project_id == project.id,
            AuditLog.target_type == "incident",
            AuditLog.action.like("jira_%"),
        )
        .order_by(AuditLog.created_at.desc())
    ).first()
    return JiraSettingsOut(
        base_url=project.jira_base_url,
        user_email=project.jira_user_email,
        api_token_configured=bool(project.jira_api_token),
        secret_lifecycle=SecretLifecycleRead(**project_secret_lifecycle_payload(session, project, SECRET_JIRA_API_TOKEN)),
        project_key=project.jira_project_key,
        issue_type=project.jira_issue_type or "Task",
        inbound_webhook_url=inbound_url,
        inbound_secret_configured=bool(os.environ.get("JIRA_WEBHOOK_SECRET")),
        inbound_timestamp_header="X-Jira-Webhook-Timestamp",
        inbound_signature_header="X-Jira-Webhook-Signature",
        inbound_signature_scheme="HMAC-SHA256 over '<timestamp>.<raw_body>' using JIRA_WEBHOOK_SECRET",
        latest_sync_action=latest_sync.action if latest_sync else None,
        latest_sync_at=latest_sync.created_at if latest_sync else None,
        configured=bool(project.jira_base_url and project.jira_user_email and project.jira_project_key and active_project_secret_candidates(project, SECRET_JIRA_API_TOKEN, session=session)),
    )


def _notification_failure_out(session: Session, row: AuditLog) -> NotificationFailureOut:
    details = {}
    try:
        parsed = json.loads(row.details or "{}")
        if isinstance(parsed, dict):
            details = parsed
    except Exception:
        details = {}
    last_retry = session.exec(
        select(AuditLog)
        .where(
            AuditLog.target_type == "audit_log",
            AuditLog.target_id == row.id,
            AuditLog.action.in_(["notification_retry", "notification_retry_failed"]),
        )
        .order_by(AuditLog.created_at.desc())
    ).first()
    return NotificationFailureOut(
        id=row.id,
        created_at=row.created_at,
        channel=str(details.get("channel") or "unknown"),
        event=str(details.get("event") or "unknown"),
        detail=details.get("detail"),
        target=details.get("target"),
        check_id=details.get("check_id"),
        subscription_id=details.get("subscription_id"),
        retryable=bool(details.get("retryable")),
        request_kind=details.get("request_kind"),
        last_retry_action=last_retry.action if last_retry else None,
        last_retry_at=last_retry.created_at if last_retry else None,
    )


@router.get("/{project_id}/pagerduty-settings", response_model=PagerDutySettingsOut)
def get_project_pagerduty_settings(
    project_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    return _pagerduty_settings_out(session, project)


@router.post("/{project_id}/pagerduty-settings", response_model=PagerDutySettingsOut)
def set_project_pagerduty_settings(
    project_id: int = Path(..., ge=1),
    payload: PagerDutySettingsIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {"pagerduty_integration_key": project.pagerduty_integration_key}
    lifecycle_kwargs = _secret_lifecycle_update_kwargs(payload)
    if payload.clear_integration_key:
        project.pagerduty_integration_key = None
        clear_project_secret_lifecycle(session, project_id, SECRET_PAGERDUTY_INTEGRATION_KEY)
    elif payload.integration_key:
        if payload.integration_key != project.pagerduty_integration_key:
            rotate_project_secret(
                session,
                project,
                SECRET_PAGERDUTY_INTEGRATION_KEY,
                new_value=payload.integration_key,
                grace_seconds=int(payload.grace_seconds or 0),
                expires_at=lifecycle_kwargs["expires_at"],
                rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
            )
        else:
            update_project_secret_policy(
                session,
                project_id,
                SECRET_PAGERDUTY_INTEGRATION_KEY,
                expires_at=lifecycle_kwargs["expires_at"],
                rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
            )
    else:
        update_project_secret_policy(
            session,
            project_id,
            SECRET_PAGERDUTY_INTEGRATION_KEY,
            expires_at=lifecycle_kwargs["expires_at"],
            rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
        )
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {"pagerduty_integration_key": project.pagerduty_integration_key}
        session.add(
            AuditLog(
                actor=actor,
                action="set_project_pagerduty_settings",
                target_type="project",
                target_id=project_id,
                details=_diff_details(before, after),
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return _pagerduty_settings_out(session, project)


@router.post("/{project_id}/pagerduty-test", response_model=PagerDutyTestResult)
def send_project_pagerduty_test(
    project_id: int = Path(..., ge=1),
    request: Request = None,
    _scope = Depends(limit_integration_action_requests),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    routing_keys = active_project_secret_candidates(project, SECRET_PAGERDUTY_INTEGRATION_KEY, session=session)
    if not routing_keys:
        raise HTTPException(status_code=400, detail="PagerDuty integration key is not configured")

    dedup_key = f"lastping:test:{project.id}:{int(datetime.utcnow().timestamp())}"
    trigger_sent = False
    matched_key = None
    for routing_key in routing_keys:
        trigger_sent = send_pagerduty_event(
            routing_key,
            f"LastPing test incident for project {project.name}",
            "info",
            event_action="trigger",
            dedup_key=dedup_key,
            source=project.name,
            component="project-settings",
            custom_details={
                "project_id": project.id,
                "project_name": project.name,
                "test_delivery": True,
            },
        )
        if trigger_sent:
            matched_key = routing_key
            break
    resolve_sent = False
    if trigger_sent and matched_key:
        resolve_sent = send_pagerduty_event(
            matched_key,
            f"LastPing test incident resolved for project {project.name}",
            "info",
            event_action="resolve",
            dedup_key=dedup_key,
            source=project.name,
            component="project-settings",
            custom_details={
                "project_id": project.id,
                "project_name": project.name,
                "test_delivery": True,
            },
        )
        touch_project_secret_last_used(project.id, SECRET_PAGERDUTY_INTEGRATION_KEY, session=session)

    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="send_project_pagerduty_test",
            target_type="project",
            target_id=project.id,
            details=json.dumps(
                {
                    "dedup_key": dedup_key,
                    "trigger_sent": trigger_sent,
                    "resolve_sent": resolve_sent,
                }
            ),
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_project_scope_kwargs(project),
        )
    )
    session.commit()

    if not trigger_sent:
        raise HTTPException(status_code=502, detail="Failed to send PagerDuty test event")

    return PagerDutyTestResult(
        ok=trigger_sent and resolve_sent,
        dedup_key=dedup_key,
        trigger_sent=trigger_sent,
        resolve_sent=resolve_sent,
        message="Sent PagerDuty test trigger and resolve events.",
    )


@router.get("/{project_id}/jira-settings", response_model=JiraSettingsOut)
def get_project_jira_settings(
    project_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    return _jira_settings_out(session, project)


@router.post("/{project_id}/jira-settings", response_model=JiraSettingsOut)
def set_project_jira_settings(
    project_id: int = Path(..., ge=1),
    payload: JiraSettingsIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    before = {
        "jira_base_url": project.jira_base_url,
        "jira_user_email": project.jira_user_email,
        "jira_api_token": project.jira_api_token,
        "jira_project_key": project.jira_project_key,
        "jira_issue_type": project.jira_issue_type,
    }
    fields_set = getattr(payload, "__fields_set__", set())
    lifecycle_kwargs = _secret_lifecycle_update_kwargs(payload)
    if "base_url" in fields_set:
        project.jira_base_url = str(payload.base_url) if payload.base_url is not None else None
    if "user_email" in fields_set:
        project.jira_user_email = str(payload.user_email) if payload.user_email is not None else None
    if payload.clear_api_token:
        project.jira_api_token = None
        clear_project_secret_lifecycle(session, project_id, SECRET_JIRA_API_TOKEN)
    elif payload.api_token:
        if payload.api_token != project.jira_api_token:
            rotate_project_secret(
                session,
                project,
                SECRET_JIRA_API_TOKEN,
                new_value=payload.api_token,
                grace_seconds=int(payload.grace_seconds or 0),
                expires_at=lifecycle_kwargs["expires_at"],
                rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
            )
        else:
            update_project_secret_policy(
                session,
                project_id,
                SECRET_JIRA_API_TOKEN,
                expires_at=lifecycle_kwargs["expires_at"],
                rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
            )
    else:
        update_project_secret_policy(
            session,
            project_id,
            SECRET_JIRA_API_TOKEN,
            expires_at=lifecycle_kwargs["expires_at"],
            rotation_interval_days=lifecycle_kwargs["rotation_interval_days"],
        )
    if "project_key" in fields_set:
        project.jira_project_key = payload.project_key.upper() if payload.project_key else None
    if "issue_type" in fields_set:
        project.jira_issue_type = payload.issue_type or "Task"
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "jira_base_url": project.jira_base_url,
            "jira_user_email": project.jira_user_email,
            "jira_api_token": project.jira_api_token,
            "jira_project_key": project.jira_project_key,
            "jira_issue_type": project.jira_issue_type,
        }
        session.add(
            AuditLog(
                actor=actor,
                action="set_project_jira_settings",
                target_type="project",
                target_id=project_id,
                details=_diff_details(before, after),
                actor_ip=actor_ip,
                user_agent=user_agent,
                **_project_scope_kwargs(project),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return _jira_settings_out(session, project)


@router.get("/{project_id}/notification-failures", response_model=List[NotificationFailureOut])
def list_project_notification_failures(
    project_id: int = Path(..., ge=1),
    limit: int = 20,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    rows = session.exec(
        select(AuditLog)
        .where(
            AuditLog.action == "notification_failed",
            AuditLog.target_type == "project",
            AuditLog.target_id == project_id,
        )
        .order_by(AuditLog.created_at.desc())
    ).all()
    capped = rows[: max(1, min(int(limit or 20), 100))]
    return [_notification_failure_out(session, row) for row in capped]


@router.post("/{project_id}/notification-failures/{failure_id}/retry", response_model=NotificationRetryResult)
def retry_project_notification_failure(
    project_id: int = Path(..., ge=1),
    failure_id: int = Path(..., ge=1),
    request: Request = None,
    _scope = Depends(limit_integration_action_requests),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    row = session.get(AuditLog, failure_id)
    if not row or row.action != "notification_failed" or row.target_type != "project" or row.target_id != project_id:
        raise HTTPException(status_code=404, detail="Notification failure not found")
    try:
        details = json.loads(row.details or "{}")
    except Exception:
        details = {}
    result = retry_notification_failure_payload(details)
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="notification_retry" if result.get("ok") else "notification_retry_failed",
            target_type="audit_log",
            target_id=row.id,
            details=json.dumps(
                {
                    "source_failure_id": row.id,
                    "target": result.get("target") or details.get("target"),
                    "retry_ok": bool(result.get("ok")),
                    "response": result.get("response"),
                    "message": result.get("detail"),
                }
            ),
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_project_scope_kwargs(project),
        )
    )
    session.commit()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("detail") or "Retry failed")
    response = result.get("response") or {}
    return NotificationRetryResult(
        ok=True,
        message="Retried webhook delivery.",
        target=result.get("target"),
        status=response.get("status"),
    )

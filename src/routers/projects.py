"""
Project management routes.

Endpoints here create projects and manage per-project webhook
configuration and API keys. `create_project` returns a plaintext API
key once; the server stores only the PBKDF2 hash.
"""

from datetime import datetime
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Path, Body
from pydantic import BaseModel, EmailStr, AnyHttpUrl, confloat, constr, root_validator
from sqlmodel import select, Session

from ..db import get_session
from ..jira import jira_settings_ready
from ..models import AuditLog, ApiKey, OrgRole, Project as ProjectModel, ProjectMembership, Role
from ..security import generate_api_key, hash_api_key
from ..alerts import retry_notification_failure_payload, send_pagerduty_event
from ..deps import authorize_org_operation, authorize_project_operation, get_audit_context, get_current_user, limit_public_requests
from ..schemas import StrictBaseModel
import os
import json


router = APIRouter(prefix="/projects", tags=["projects"])


def _diff_details(before: dict, after: dict) -> Optional[str]:
    changes = {}
    for key, old_val in before.items():
        new_val = after.get(key)
        if old_val != new_val:
            changes[key] = {"from": old_val, "to": new_val}
    if not changes:
        return None
    try:
        return json.dumps(changes, default=str)
    except Exception:
        return str(changes)


class ProjectCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    owner_email: Optional[EmailStr] = None
    org_id: Optional[int] = None


class ProjectRead(BaseModel):
    id: int
    name: str
    org_id: Optional[int] = None
    created_at: datetime
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    pagerduty_integration_key: Optional[str] = None
    generic_webhook_url: Optional[str] = None
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


class ProjectTokenRead(BaseModel):
    id: int
    project_id: int
    name: Optional[str]
    role: str
    is_active: bool
    revoked_at: Optional[datetime]
    rate_limit_per_minute: Optional[int]
    created_by_user_id: Optional[int]
    created_at: datetime

    class Config:
        orm_mode = True


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
    authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    rows = session.exec(select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())).all()
    return [ProjectTokenRead.from_orm(row) for row in rows]


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
    return {"token": ProjectTokenRead.from_orm(token), "api_key": plain}


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
    if token.key_hash == project.api_key_hash or token.name == "primary":
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
    return ProjectTokenRead.from_orm(token)


class WebhookUpdate(StrictBaseModel):
    discord_webhook_url: Optional[AnyHttpUrl] = None
    slack_webhook_url: Optional[AnyHttpUrl] = None
    slack_channel: Optional[constr(max_length=120)] = None
    pagerduty_integration_key: Optional[constr(max_length=128)] = None
    generic_webhook_url: Optional[AnyHttpUrl] = None


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


class PagerDutySettingsOut(BaseModel):
    integration_key: Optional[str] = None
    integration_key_configured: bool
    inbound_webhook_url: str
    inbound_secret_configured: bool
    inbound_secret_header: str
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


class JiraSettingsOut(BaseModel):
    base_url: Optional[str] = None
    user_email: Optional[str] = None
    api_token: Optional[str] = None
    project_key: Optional[str] = None
    issue_type: Optional[str] = None
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


@router.get("/{project_id}/webhooks", response_model=WebhookUpdate, dependencies=[Depends(limit_public_requests)])
def get_project_webhooks(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return WebhookUpdate(
        discord_webhook_url=project.discord_webhook_url,
        slack_webhook_url=project.slack_webhook_url,
        slack_channel=project.slack_channel,
        pagerduty_integration_key=project.pagerduty_integration_key,
        generic_webhook_url=project.generic_webhook_url,
    )


@router.post("/{project_id}/webhooks", response_model=WebhookUpdate)
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
    project.discord_webhook_url = payload.discord_webhook_url
    project.slack_webhook_url = payload.slack_webhook_url
    project.slack_channel = payload.slack_channel
    project.pagerduty_integration_key = payload.pagerduty_integration_key
    project.generic_webhook_url = payload.generic_webhook_url
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
    return payload


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
def rotate_api_key(project_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    project = authorize_project_operation(
        project_id,
        min_role=Role.OWNER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    new_key = generate_api_key()
    old_hash = getattr(project, "api_key_hash", None)
    new_hash = hash_api_key(new_key)
    project.api_key_hash = new_hash
    # Keep `api_key` table in sync for the primary key.
    try:
        if old_hash:
            ak = session.exec(select(ApiKey).where(ApiKey.project_id == project_id, ApiKey.key_hash == old_hash)).first()
        else:
            ak = None
        if ak:
            ak.key_hash = new_hash
            session.add(ak)
        else:
            session.add(ApiKey(project_id=project_id, key_hash=new_hash, name="primary", role=Role.OWNER.value, rate_limit_per_minute=0))
    except Exception:
        pass
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
    # audit
    try:
        from ..models import AuditLog
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor or "project_api", action="rotate_primary_api_key", target_type="project", target_id=project_id, details=None, actor_ip=actor_ip, user_agent=user_agent, **_project_scope_kwargs(project))
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"api_key": new_key}


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
        p.api_key_hash = new_hash
        # Keep `api_key` table in sync for the primary key.
        try:
            if old_hash:
                ak = session.exec(select(ApiKey).where(ApiKey.project_id == p.id, ApiKey.key_hash == old_hash)).first()
            else:
                ak = None
            if ak:
                ak.key_hash = new_hash
                session.add(ak)
            else:
                session.add(ApiKey(project_id=p.id, key_hash=new_hash, name="primary", role=Role.OWNER.value, rate_limit_per_minute=0))
        except Exception:
            pass
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


@router.get("/{project_id}/alert-settings", response_model=AlertSettings, dependencies=[Depends(limit_public_requests)])
def get_project_alert_settings(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
        integration_key=project.pagerduty_integration_key,
        integration_key_configured=bool(project.pagerduty_integration_key),
        inbound_webhook_url=inbound_url,
        inbound_secret_configured=bool(os.environ.get("PAGERDUTY_WEBHOOK_SECRET")),
        inbound_secret_header="X-PagerDuty-Webhook-Secret",
        latest_sync_action=latest_sync.action if latest_sync else None,
        latest_sync_at=latest_sync.created_at if latest_sync else None,
    )


def _jira_settings_out(project: ProjectModel) -> JiraSettingsOut:
    return JiraSettingsOut(
        base_url=project.jira_base_url,
        user_email=project.jira_user_email,
        api_token=project.jira_api_token,
        project_key=project.jira_project_key,
        issue_type=project.jira_issue_type or "Task",
        configured=jira_settings_ready(project),
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
    project.pagerduty_integration_key = payload.integration_key
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
    routing_key = (project.pagerduty_integration_key or "").strip()
    if not routing_key:
        raise HTTPException(status_code=400, detail="PagerDuty integration key is not configured")

    dedup_key = f"lastping:test:{project.id}:{int(datetime.utcnow().timestamp())}"
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
    resolve_sent = False
    if trigger_sent:
        resolve_sent = send_pagerduty_event(
            routing_key,
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
    return _jira_settings_out(project)


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
    project.jira_base_url = str(payload.base_url) if payload.base_url is not None else None
    project.jira_user_email = str(payload.user_email) if payload.user_email is not None else None
    project.jira_api_token = payload.api_token
    project.jira_project_key = payload.project_key.upper() if payload.project_key else None
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
    return _jira_settings_out(project)


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

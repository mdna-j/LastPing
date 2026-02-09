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
from ..models import Project as ProjectModel, AuditLog, ApiKey
from ..security import generate_api_key, hash_api_key
from ..deps import limit_by_api_key, get_audit_context, limit_public_requests
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


class ProjectRead(BaseModel):
    id: int
    name: str
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


@router.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_public_requests)])
def create_project(payload: ProjectCreate, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Create a new project and return the project and API key."""
    # generate one-time plaintext API key and store only the hash
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    project = ProjectModel(name=payload.name, api_key_hash=api_key_hash, owner_email=payload.owner_email)
    session.add(project)
    session.commit()
    session.refresh(project)
    # Ensure the primary project API key is also represented in the `api_key`
    # table so write endpoints guarded by `limit_by_api_key` accept it.
    try:
        session.add(ApiKey(project_id=project.id, key_hash=api_key_hash, rate_limit_per_minute=0))
        session.commit()
    except Exception:
        session.rollback()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_project", target_type="project", target_id=project.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
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


class WebhookUpdate(StrictBaseModel):
    discord_webhook_url: Optional[AnyHttpUrl] = None
    slack_webhook_url: Optional[AnyHttpUrl] = None
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
        pagerduty_integration_key=project.pagerduty_integration_key,
        generic_webhook_url=project.generic_webhook_url,
    )


@router.post("/{project_id}/webhooks", response_model=WebhookUpdate)
def update_project_webhooks(project_id: int = Path(..., ge=1), payload: WebhookUpdate = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    before = {
        "discord_webhook_url": project.discord_webhook_url,
        "slack_webhook_url": project.slack_webhook_url,
        "pagerduty_integration_key": project.pagerduty_integration_key,
        "generic_webhook_url": project.generic_webhook_url,
    }
    project.discord_webhook_url = payload.discord_webhook_url
    project.slack_webhook_url = payload.slack_webhook_url
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
            "pagerduty_integration_key": project.pagerduty_integration_key,
            "generic_webhook_url": project.generic_webhook_url,
        }
        al = AuditLog(actor=actor, action="update_project_webhooks", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
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
def set_project_maintenance(project_id: int = Path(..., ge=1), payload: MaintenanceWindow = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
        al = AuditLog(actor=actor, action="set_project_maintenance", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return payload


@router.post("/{project_id}/rotate-key")
def rotate_api_key(project_id: int = Path(..., ge=1), request: Request = None, session: Session = Depends(get_session), _rl = Depends(limit_by_api_key)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
            session.add(ApiKey(project_id=project_id, key_hash=new_hash, rate_limit_per_minute=0))
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
        actor, actor_ip, user_agent = get_audit_context(request, None, None, session)
        al = AuditLog(actor=actor or "project_api", action="rotate_primary_api_key", target_type="project", target_id=project_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
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
                session.add(ApiKey(project_id=p.id, key_hash=new_hash, rate_limit_per_minute=0))
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
            al = AuditLog(actor=actor, action="rotate_project_key_admin", target_type="project", target_id=p.id, details="rotate_all_keys", actor_ip=actor_ip, user_agent=user_agent)
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
def set_project_slo(project_id: int = Path(..., ge=1), payload: SloSettings = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
        al = AuditLog(actor=actor, action="set_project_slo", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
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
def set_project_alert_settings(project_id: int = Path(..., ge=1), payload: AlertSettings = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
        al = AuditLog(actor=actor, action="set_project_alert_settings", target_type="project", target_id=project_id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
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

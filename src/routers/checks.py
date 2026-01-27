"""
Checks CRUD routes.

Create and list monitoring checks for a project. Checks may be
heartbeat-based or HTTP checks; the worker interprets check fields to
drive scheduling and detection logic.
"""

from typing import List, Optional, Literal
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Path, Body
from pydantic import BaseModel, AnyHttpUrl, conint, constr, root_validator
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, CheckType, CheckStatus, Project, AuditLog
from ..deps import require_admin_or_project_api_key, get_current_user, require_project_role, limit_by_api_key, get_audit_context, limit_public_requests
from ..schemas import StrictBaseModel


router = APIRouter(prefix="/projects/{project_id}/checks", tags=["checks"])


class CheckCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    type: Optional[Literal["heartbeat", "http", "tcp", "dns"]] = CheckType.HEARTBEAT
    expected_interval: Optional[conint(ge=1, le=86400)] = 600
    grace_period: Optional[conint(ge=0, le=86400)] = 600
    url: Optional[AnyHttpUrl] = None
    timeout: Optional[conint(ge=1, le=60)] = 5
    retries: Optional[conint(ge=0, le=10)] = 1
    host: Optional[constr(min_length=1, max_length=253)] = None
    port: Optional[conint(ge=1, le=65535)] = None
    dns_record_type: Optional[constr(regex=r"^[A-Za-z0-9_-]{1,10}$")] = None
    interval: Optional[conint(ge=1, le=86400)] = 60
    latency_threshold_ms: Optional[conint(ge=1, le=600000)] = None
    region: Optional[constr(regex=r"^[A-Za-z0-9._-]{1,32}$")] = None
    alert_enabled: Optional[bool] = True
    alert_after: Optional[conint(ge=1, le=1000)] = 1
    alert_cooldown: Optional[conint(ge=0, le=86400)] = 3600
    escalation_after_minutes: Optional[conint(ge=1, le=10080)] = None
    escalation_cooldown_seconds: Optional[conint(ge=0, le=86400)] = 3600

    @root_validator
    def _validate_by_type(cls, values):
        ctype = values.get("type") or CheckType.HEARTBEAT
        if isinstance(ctype, str):
            ctype = ctype.lower()
            values["type"] = ctype
        if ctype == "http" and not values.get("url"):
            raise ValueError("url is required for http checks")
        if ctype == "tcp":
            if not values.get("host"):
                raise ValueError("host is required for tcp checks")
            if not values.get("port"):
                raise ValueError("port is required for tcp checks")
        if ctype == "dns":
            if not values.get("host"):
                raise ValueError("host is required for dns checks")
            if not values.get("dns_record_type"):
                raise ValueError("dns_record_type is required for dns checks")
        return values


class CheckRead(BaseModel):
    id: int
    project_id: int
    name: str
    type: str
    status: str
    last_ping: Optional[datetime]
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    dns_record_type: Optional[str] = None
    interval: Optional[int] = None
    latency_threshold_ms: Optional[int] = None
    last_latency_ms: Optional[float] = None
    region: Optional[str] = None
    alert_enabled: Optional[bool] = None
    alert_after: Optional[int] = None
    alert_cooldown: Optional[int] = None
    escalation_after_minutes: Optional[int] = None
    escalation_cooldown_seconds: Optional[int] = None

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CheckRead)
def create_check(project_id: int = Path(..., ge=1), payload: CheckCreate = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    """Create a check for the given project.

    Names must be unique within a project. HTTP checks should provide a
    `url`; heartbeat checks are created automatically by the heartbeat
    endpoint on first use as well.
    """
    # allow either admin/project API key OR a user with owner role
    try:
        project = require_admin_or_project_api_key(project_id, x_admin_token=x_admin_token, authorization=authorization, x_api_key=x_api_key, session=session)
    except HTTPException:
        # fallback to user token + project membership (owner)
        user = get_current_user(authorization=authorization, session=session)
        require_project_role(project_id, 'owner', current_user=user, session=session)

    # ensure name uniqueness within project
    existing = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Check with that name already exists")

    check = CheckModel(
        project_id=project_id,
        name=payload.name,
        type=payload.type,
        expected_interval=payload.expected_interval,
        grace_period=payload.grace_period,
        url=payload.url,
        host=payload.host,
        port=payload.port,
        dns_record_type=payload.dns_record_type,
        timeout=payload.timeout,
        retries=payload.retries,
        interval=payload.interval,
        latency_threshold_ms=payload.latency_threshold_ms,
        region=payload.region,
        alert_enabled=payload.alert_enabled if payload.alert_enabled is not None else True,
        alert_after=payload.alert_after,
        alert_cooldown=payload.alert_cooldown,
        escalation_after_minutes=payload.escalation_after_minutes,
        escalation_cooldown_seconds=payload.escalation_cooldown_seconds,
        status=CheckStatus.UP,
    )
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_check", target_type="check", target_id=check.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return check


class CheckUpdate(StrictBaseModel):
    name: Optional[constr(min_length=1, max_length=120)] = None
    url: Optional[AnyHttpUrl] = None
    interval: Optional[conint(ge=1, le=86400)] = None
    expected_interval: Optional[conint(ge=1, le=86400)] = None
    grace_period: Optional[conint(ge=0, le=86400)] = None
    host: Optional[constr(min_length=1, max_length=253)] = None
    port: Optional[conint(ge=1, le=65535)] = None
    dns_record_type: Optional[constr(regex=r"^[A-Za-z0-9_-]{1,10}$")] = None
    latency_threshold_ms: Optional[conint(ge=1, le=600000)] = None
    region: Optional[constr(regex=r"^[A-Za-z0-9._-]{1,32}$")] = None
    alert_enabled: Optional[bool] = None
    alert_after: Optional[conint(ge=1, le=1000)] = None
    alert_cooldown: Optional[conint(ge=0, le=86400)] = None
    escalation_after_minutes: Optional[conint(ge=1, le=10080)] = None
    escalation_cooldown_seconds: Optional[conint(ge=0, le=86400)] = None


@router.put("/{check_id}", response_model=CheckRead)
def update_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), payload: CheckUpdate = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # require owner/admin/api-key
    try:
        project = require_admin_or_project_api_key(project_id, x_admin_token=x_admin_token, authorization=authorization, x_api_key=x_api_key, session=session)
    except HTTPException:
        user = get_current_user(authorization=authorization, session=session)
        require_project_role(project_id, 'owner', current_user=user, session=session)
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    if payload.name is not None:
        # ensure name uniqueness
        existing = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == payload.name, CheckModel.id != check_id)).first()
        if existing:
            raise HTTPException(status_code=409, detail="Check with that name already exists")
        check.name = payload.name
    if payload.url is not None:
        check.url = payload.url
    if payload.interval is not None:
        check.interval = payload.interval
    if payload.expected_interval is not None:
        check.expected_interval = payload.expected_interval
    if payload.grace_period is not None:
        check.grace_period = payload.grace_period
    if payload.host is not None:
        check.host = payload.host
    if payload.port is not None:
        check.port = payload.port
    if payload.dns_record_type is not None:
        check.dns_record_type = payload.dns_record_type
    if payload.latency_threshold_ms is not None:
        check.latency_threshold_ms = payload.latency_threshold_ms
    if payload.region is not None:
        check.region = payload.region
    if payload.alert_enabled is not None:
        check.alert_enabled = payload.alert_enabled
    if payload.alert_after is not None:
        check.alert_after = payload.alert_after
    if payload.alert_cooldown is not None:
        check.alert_cooldown = payload.alert_cooldown
    if payload.escalation_after_minutes is not None:
        check.escalation_after_minutes = payload.escalation_after_minutes
    if payload.escalation_cooldown_seconds is not None:
        check.escalation_cooldown_seconds = payload.escalation_cooldown_seconds
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="update_check", target_type="check", target_id=check.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return check


@router.delete("/{check_id}")
def delete_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        project = require_admin_or_project_api_key(project_id, x_admin_token=x_admin_token, authorization=authorization, x_api_key=x_api_key, session=session)
    except HTTPException:
        user = get_current_user(authorization=authorization, session=session)
        require_project_role(project_id, 'owner', current_user=user, session=session)
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    session.delete(check)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_check", target_type="check", target_id=check_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/", response_model=List[CheckRead], dependencies=[Depends(limit_public_requests)])
def list_checks(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    """List checks for a project (minimal visibility endpoint)."""
    checks = session.exec(select(CheckModel).where(CheckModel.project_id == project_id)).all()
    return checks


@router.get("/{check_id}", response_model=CheckRead, dependencies=[Depends(limit_public_requests)])
def get_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return check


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


@router.get("/{check_id}/maintenance", response_model=MaintenanceWindow, dependencies=[Depends(limit_public_requests)])
def get_check_maintenance(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return MaintenanceWindow(maintenance_starts_at=check.maintenance_starts_at, maintenance_ends_at=check.maintenance_ends_at)


@router.post("/{check_id}/maintenance", response_model=MaintenanceWindow)
def set_check_maintenance(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), payload: MaintenanceWindow = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        project = require_admin_or_project_api_key(project_id, x_admin_token=x_admin_token, authorization=authorization, x_api_key=x_api_key, session=session)
    except HTTPException:
        user = get_current_user(authorization=authorization, session=session)
        require_project_role(project_id, 'owner', current_user=user, session=session)
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    check.maintenance_starts_at = payload.maintenance_starts_at
    check.maintenance_ends_at = payload.maintenance_ends_at
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="set_check_maintenance", target_type="check", target_id=check.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return payload

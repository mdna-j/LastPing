from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Path, Query, Body
from pydantic import BaseModel, AnyHttpUrl, conint, constr
from sqlmodel import Session, select

from ..db import get_session
from ..models import RemediationHook, RemediationLog, RemediationApproval, Project, AuditLog
from ..deps import require_admin_or_owner, require_project_api_key, limit_by_api_key, get_audit_context
from ..schemas import StrictBaseModel

router = APIRouter(prefix="/projects/{project_id}/remediation", tags=["remediation"])


class RemediationHookIn(StrictBaseModel):
    check_id: Optional[conint(ge=1)] = None
    event_type: constr(regex=r"^(down|degraded)$") = "down"
    url: AnyHttpUrl
    method: Optional[constr(regex=r"(?i)^(GET|POST|PUT|PATCH|DELETE)$")] = "POST"
    enabled: Optional[bool] = True
    cooldown_seconds: Optional[conint(ge=0, le=86400)] = 900
    secret: Optional[constr(max_length=255)] = None
    require_secret: Optional[bool] = False
    require_approval: Optional[bool] = False
    max_triggers_per_day: Optional[conint(ge=1, le=10000)] = 50
    disable_on_failure_count: Optional[conint(ge=1, le=1000)] = 5
    allow_during_maintenance: Optional[bool] = False


class RemediationHookOut(BaseModel):
    id: int
    project_id: int
    check_id: Optional[int]
    event_type: str
    url: str
    method: str
    enabled: bool
    cooldown_seconds: int
    last_triggered_at: Optional[str]
    require_secret: bool
    require_approval: bool
    max_triggers_per_day: Optional[int]
    failure_count: int
    disable_on_failure_count: Optional[int]
    disabled_at: Optional[str]
    disabled_reason: Optional[str]
    allow_during_maintenance: bool

    class Config:
        orm_mode = True


@router.get("/hooks", response_model=List[RemediationHookOut])
def list_hooks(project_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    rows = session.exec(select(RemediationHook).where(RemediationHook.project_id == project_id)).all()
    return rows


@router.post("/hooks", response_model=RemediationHookOut, status_code=status.HTTP_201_CREATED)
def create_hook(project_id: int = Path(..., ge=1), payload: RemediationHookIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    hook = RemediationHook(
        project_id=project_id,
        check_id=payload.check_id,
        event_type=payload.event_type,
        url=payload.url,
        method=(payload.method or "POST").upper(),
        enabled=payload.enabled if payload.enabled is not None else True,
        cooldown_seconds=payload.cooldown_seconds or 900,
        secret=payload.secret,
        require_secret=payload.require_secret if payload.require_secret is not None else False,
        require_approval=payload.require_approval if payload.require_approval is not None else False,
        max_triggers_per_day=payload.max_triggers_per_day,
        disable_on_failure_count=payload.disable_on_failure_count,
        allow_during_maintenance=payload.allow_during_maintenance if payload.allow_during_maintenance is not None else False,
    )
    session.add(hook)
    session.commit()
    session.refresh(hook)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_remediation_hook", target_type="remediation_hook", target_id=hook.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return hook


@router.delete("/hooks/{hook_id}")
def delete_hook(project_id: int = Path(..., ge=1), hook_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    hook = session.get(RemediationHook, hook_id)
    if not hook or hook.project_id != project_id:
        raise HTTPException(status_code=404, detail="Hook not found")
    session.delete(hook)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_remediation_hook", target_type="remediation_hook", target_id=hook_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/logs")
def list_logs(project_id: int = Path(..., ge=1), limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    rows = session.exec(select(RemediationLog).where(RemediationLog.project_id == project_id).order_by(RemediationLog.created_at.desc()).limit(limit)).all()
    return rows


class RemediationApprovalOut(BaseModel):
    id: int
    hook_id: int
    project_id: int
    check_id: int
    event_type: str
    reason: Optional[str]
    status: str
    requested_at: Optional[str]
    decided_at: Optional[str]
    decided_by: Optional[str]
    expires_at: Optional[str]
    executed_at: Optional[str]
    execution_status: Optional[str]
    execution_message: Optional[str]

    class Config:
        orm_mode = True


@router.get("/approvals", response_model=List[RemediationApprovalOut])
def list_approvals(project_id: int = Path(..., ge=1), status_filter: Optional[str] = Query(None, max_length=32), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    stmt = select(RemediationApproval).where(RemediationApproval.project_id == project_id)
    if status_filter:
        stmt = stmt.where(RemediationApproval.status == status_filter)
    return session.exec(stmt.order_by(RemediationApproval.requested_at.desc())).all()


@router.post("/approvals/{approval_id}/approve", response_model=RemediationApprovalOut)
def approve_approval(project_id: int = Path(..., ge=1), approval_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    approval = session.get(RemediationApproval, approval_id)
    if not approval or approval.project_id != project_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        return approval
    approval.status = "approved"
    approval.decided_at = datetime.utcnow()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        approval.decided_by = actor
        al = AuditLog(actor=actor, action="approve_remediation", target_type="remediation_approval", target_id=approval.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
    except Exception:
        pass
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


@router.post("/approvals/{approval_id}/deny", response_model=RemediationApprovalOut)
def deny_approval(project_id: int = Path(..., ge=1), approval_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    approval = session.get(RemediationApproval, approval_id)
    if not approval or approval.project_id != project_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        return approval
    approval.status = "denied"
    approval.decided_at = datetime.utcnow()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        approval.decided_by = actor
        al = AuditLog(actor=actor, action="deny_remediation", target_type="remediation_approval", target_id=approval.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
    except Exception:
        pass
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval

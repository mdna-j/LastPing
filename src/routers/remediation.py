from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Path, Query, Body
from pydantic import BaseModel, AnyHttpUrl, conint, constr
from sqlmodel import Session, select

from ..db import get_session
from ..models import RemediationHook, RemediationLog, Project, AuditLog
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

from datetime import datetime
from typing import Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Path, Query, Body
from pydantic import BaseModel, EmailStr, conint, constr, root_validator
from sqlmodel import Session, select

from ..db import get_session
from ..models import OnCallRotation, OnCallMember, OnCallEscalation, OnCallAlert, Project, AuditLog
from ..deps import require_admin_or_owner, require_project_api_key, limit_by_api_key, get_audit_context
from ..schemas import StrictBaseModel

router = APIRouter(prefix="/projects/{project_id}/oncall", tags=["oncall"])


class RotationIn(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    interval_minutes: Optional[conint(ge=1, le=10080)] = 1440
    start_at: Optional[datetime] = None
    enabled: Optional[bool] = True


class MemberIn(StrictBaseModel):
    rotation_id: conint(ge=1)
    name: constr(min_length=1, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[constr(regex=r"^\\+?[0-9]{7,20}$")] = None
    order: Optional[conint(ge=0, le=1000)] = 0
    active: Optional[bool] = True


class EscalationIn(StrictBaseModel):
    level: conint(ge=0, le=20)
    delay_minutes: Optional[conint(ge=0, le=1440)] = 15
    target_type: Literal["rotation", "email", "sms"]
    rotation_id: Optional[conint(ge=1)] = None
    target_value: Optional[constr(max_length=200)] = None
    enabled: Optional[bool] = True

    @root_validator
    def _validate_target(cls, values):
        ttype = values.get("target_type")
        rotation_id = values.get("rotation_id")
        target_value = values.get("target_value")
        if ttype == "rotation" and not rotation_id:
            raise ValueError("rotation_id is required when target_type=rotation")
        if ttype in ("email", "sms") and not target_value:
            raise ValueError("target_value is required for email/sms targets")
        return values


@router.get("/rotations")
def list_rotations(project_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallRotation).where(OnCallRotation.project_id == project_id)).all()


@router.post("/rotations", status_code=status.HTTP_201_CREATED)
def create_rotation(project_id: int = Path(..., ge=1), payload: RotationIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    rot = OnCallRotation(
        project_id=project_id,
        name=payload.name,
        interval_minutes=payload.interval_minutes or 1440,
        start_at=payload.start_at or datetime.utcnow(),
        enabled=payload.enabled if payload.enabled is not None else True,
    )
    session.add(rot)
    session.commit()
    session.refresh(rot)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_oncall_rotation", target_type="oncall_rotation", target_id=rot.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return rot


@router.delete("/rotations/{rotation_id}")
def delete_rotation(project_id: int = Path(..., ge=1), rotation_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    rot = session.get(OnCallRotation, rotation_id)
    if not rot or rot.project_id != project_id:
        raise HTTPException(status_code=404, detail="Rotation not found")
    session.delete(rot)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_oncall_rotation", target_type="oncall_rotation", target_id=rotation_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/rotations/{rotation_id}/members")
def list_members(project_id: int = Path(..., ge=1), rotation_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallMember).where(OnCallMember.rotation_id == rotation_id).order_by(OnCallMember.order)).all()


@router.post("/rotations/{rotation_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(project_id: int = Path(..., ge=1), rotation_id: int = Path(..., ge=1), payload: MemberIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    member = OnCallMember(
        rotation_id=rotation_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        order=payload.order or 0,
        active=payload.active if payload.active is not None else True,
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="add_oncall_member", target_type="oncall_member", target_id=member.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return member


@router.delete("/rotations/{rotation_id}/members/{member_id}")
def delete_member(project_id: int = Path(..., ge=1), rotation_id: int = Path(..., ge=1), member_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    member = session.get(OnCallMember, member_id)
    if not member or member.rotation_id != rotation_id:
        raise HTTPException(status_code=404, detail="Member not found")
    session.delete(member)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_oncall_member", target_type="oncall_member", target_id=member_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/escalations")
def list_escalations(project_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallEscalation).where(OnCallEscalation.project_id == project_id).order_by(OnCallEscalation.level)).all()


@router.post("/escalations", status_code=status.HTTP_201_CREATED)
def create_escalation(project_id: int = Path(..., ge=1), payload: EscalationIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    esc = OnCallEscalation(
        project_id=project_id,
        level=payload.level,
        delay_minutes=payload.delay_minutes or 15,
        target_type=payload.target_type,
        rotation_id=payload.rotation_id,
        target_value=payload.target_value,
        enabled=payload.enabled if payload.enabled is not None else True,
    )
    session.add(esc)
    session.commit()
    session.refresh(esc)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_oncall_escalation", target_type="oncall_escalation", target_id=esc.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return esc


@router.delete("/escalations/{escalation_id}")
def delete_escalation(project_id: int = Path(..., ge=1), escalation_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    esc = session.get(OnCallEscalation, escalation_id)
    if not esc or esc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Escalation not found")
    session.delete(esc)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_oncall_escalation", target_type="oncall_escalation", target_id=escalation_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/alerts")
def list_oncall_alerts(project_id: int = Path(..., ge=1), status_filter: Optional[str] = Query(None, max_length=32), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    stmt = select(OnCallAlert).where(OnCallAlert.project_id == project_id)
    if status_filter:
        stmt = stmt.where(OnCallAlert.status == status_filter)
    return session.exec(stmt.order_by(OnCallAlert.created_at.desc())).all()


@router.post("/alerts/{alert_id}/close")
def close_alert(project_id: int = Path(..., ge=1), alert_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    alert = session.get(OnCallAlert, alert_id)
    if not alert or alert.project_id != project_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = "closed"
    session.add(alert)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="close_oncall_alert", target_type="oncall_alert", target_id=alert_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "closed"}

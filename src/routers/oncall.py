from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import OnCallRotation, OnCallMember, OnCallEscalation, OnCallAlert, Project, AuditLog
from ..deps import require_admin_or_owner, require_project_api_key, limit_by_api_key, get_audit_context

router = APIRouter(prefix="/projects/{project_id}/oncall", tags=["oncall"])


class RotationIn(BaseModel):
    name: str
    interval_minutes: Optional[int] = 1440
    start_at: Optional[datetime] = None
    enabled: Optional[bool] = True


class MemberIn(BaseModel):
    rotation_id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    order: Optional[int] = 0
    active: Optional[bool] = True


class EscalationIn(BaseModel):
    level: int
    delay_minutes: Optional[int] = 15
    target_type: str
    rotation_id: Optional[int] = None
    target_value: Optional[str] = None
    enabled: Optional[bool] = True


@router.get("/rotations")
def list_rotations(project_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallRotation).where(OnCallRotation.project_id == project_id)).all()


@router.post("/rotations", status_code=status.HTTP_201_CREATED)
def create_rotation(project_id: int, payload: RotationIn, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
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
def delete_rotation(project_id: int, rotation_id: int, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
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
def list_members(project_id: int, rotation_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallMember).where(OnCallMember.rotation_id == rotation_id).order_by(OnCallMember.order)).all()


@router.post("/rotations/{rotation_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(project_id: int, rotation_id: int, payload: MemberIn, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
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
def delete_member(project_id: int, rotation_id: int, member_id: int, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
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
def list_escalations(project_id: int, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    return session.exec(select(OnCallEscalation).where(OnCallEscalation.project_id == project_id).order_by(OnCallEscalation.level)).all()


@router.post("/escalations", status_code=status.HTTP_201_CREATED)
def create_escalation(project_id: int, payload: EscalationIn, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
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
def delete_escalation(project_id: int, escalation_id: int, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
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
def list_oncall_alerts(project_id: int, status_filter: Optional[str] = None, session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    stmt = select(OnCallAlert).where(OnCallAlert.project_id == project_id)
    if status_filter:
        stmt = stmt.where(OnCallAlert.status == status_filter)
    return session.exec(stmt.order_by(OnCallAlert.created_at.desc())).all()


@router.post("/alerts/{alert_id}/close")
def close_alert(project_id: int, alert_id: int, request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
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

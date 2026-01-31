from datetime import datetime
from typing import Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Path, Query, Body
from pydantic import BaseModel, EmailStr, conint, constr, root_validator, validator
from sqlmodel import Session, select

from ..db import get_session
from ..models import OnCallRotation, OnCallMember, OnCallEscalation, OnCallAlert, Project, AuditLog, Check
from ..deps import require_admin_or_owner, require_project_api_key, limit_by_api_key, get_audit_context
from ..schemas import StrictBaseModel

router = APIRouter(prefix="/projects/{project_id}/oncall", tags=["oncall"])

_ALLOWED_EVENT_TYPES = {"down", "degraded"}


def _normalize_event_types(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    invalid = [p for p in parts if p not in _ALLOWED_EVENT_TYPES]
    if invalid:
        raise ValueError(f"event_types must be one of: {', '.join(sorted(_ALLOWED_EVENT_TYPES))}")
    seen = set()
    ordered = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ",".join(ordered) if ordered else None


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
    check_id: Optional[conint(ge=1)] = None
    rotation_id: Optional[conint(ge=1)] = None
    target_value: Optional[constr(max_length=200)] = None
    enabled: Optional[bool] = True
    event_types: Optional[constr(max_length=64)] = None

    @validator("event_types")
    def _validate_event_types(cls, v):
        return _normalize_event_types(v)

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


class EscalationPatch(StrictBaseModel):
    level: Optional[conint(ge=0, le=20)] = None
    delay_minutes: Optional[conint(ge=0, le=1440)] = None
    target_type: Optional[Literal["rotation", "email", "sms"]] = None
    rotation_id: Optional[conint(ge=1)] = None
    target_value: Optional[constr(max_length=200)] = None
    enabled: Optional[bool] = None
    event_types: Optional[constr(max_length=64)] = None

    @validator("event_types")
    def _validate_event_types(cls, v):
        return _normalize_event_types(v)


class EscalationTemplateIn(StrictBaseModel):
    source_check_id: Optional[conint(ge=1)] = None
    target_check_id: Optional[conint(ge=1)] = None
    overwrite: Optional[bool] = True


class SmsSettingsIn(StrictBaseModel):
    sms_provider: Optional[Literal["twilio"]] = None
    sms_account_sid: Optional[constr(max_length=128)] = None
    sms_auth_token: Optional[constr(max_length=128)] = None
    sms_from: Optional[constr(regex=r"^\\+?[0-9]{7,20}$")] = None


class SmsSettingsOut(BaseModel):
    sms_provider: Optional[str] = None
    sms_account_sid: Optional[str] = None
    sms_auth_token_set: bool = False
    sms_from: Optional[str] = None

    class Config:
        orm_mode = True


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
def list_escalations(project_id: int = Path(..., ge=1), check_id: Optional[int] = Query(None, ge=1), session: Session = Depends(get_session), _proj: Project = Depends(require_project_api_key)):
    stmt = select(OnCallEscalation).where(OnCallEscalation.project_id == project_id)
    if check_id is not None:
        stmt = stmt.where(OnCallEscalation.check_id == check_id)
    return session.exec(stmt.order_by(OnCallEscalation.level)).all()


@router.get("/escalations/preview")
def preview_escalations(
    project_id: int = Path(..., ge=1),
    check_id: Optional[int] = Query(None, ge=1),
    event_type: Optional[Literal["down", "degraded"]] = Query(None),
    session: Session = Depends(get_session),
    _proj: Project = Depends(require_project_api_key),
):
    def _fetch(cid):
        stmt = select(OnCallEscalation).where(
            OnCallEscalation.project_id == project_id,
            OnCallEscalation.enabled == True,
            OnCallEscalation.check_id == cid,
        )
        return session.exec(stmt.order_by(OnCallEscalation.level)).all()

    def _filter(escalations):
        if not event_type:
            return escalations
        et = event_type.lower()
        out = []
        for e in escalations:
            raw = getattr(e, "event_types", None)
            if not raw:
                out.append(e)
                continue
            parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
            if et in parts:
                out.append(e)
        return out

    source = "project"
    escs = []
    if check_id is not None:
        escs = _filter(_fetch(check_id))
        if escs:
            source = "check"
        else:
            escs = _filter(_fetch(None))
    else:
        escs = _filter(_fetch(None))
    if not escs:
        return {"check_id": check_id, "source": source, "steps": []}

    steps = {}
    for e in escs:
        lvl = e.level or 0
        steps.setdefault(lvl, []).append(e)
    ordered = []
    for lvl in sorted(steps.keys()):
        group = steps[lvl]
        delay = group[0].delay_minutes if group else 0
        ordered.append({
            "level": lvl,
            "delay_minutes": delay,
            "event_types": group[0].event_types if group else None,
            "channels": [
                {
                    "id": g.id,
                    "target_type": g.target_type,
                    "rotation_id": g.rotation_id,
                    "target_value": g.target_value,
                    "enabled": g.enabled,
                }
                for g in group
            ],
        })
    return {"check_id": check_id, "source": source, "steps": ordered}


@router.post("/escalations/apply-template")
def apply_template(
    project_id: int = Path(..., ge=1),
    payload: EscalationTemplateIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    _rl = Depends(limit_by_api_key),
    session: Session = Depends(get_session),
):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    source_check_id = payload.source_check_id
    target_check_id = payload.target_check_id
    if source_check_id is None and target_check_id is None:
        raise HTTPException(status_code=400, detail="source_check_id or target_check_id is required")
    if source_check_id is not None:
        src_check = session.get(Check, source_check_id)
        if not src_check or src_check.project_id != project_id:
            raise HTTPException(status_code=404, detail="Source check not found for project")
    if target_check_id is not None:
        tgt_check = session.get(Check, target_check_id)
        if not tgt_check or tgt_check.project_id != project_id:
            raise HTTPException(status_code=404, detail="Target check not found for project")

    stmt = select(OnCallEscalation).where(OnCallEscalation.project_id == project_id, OnCallEscalation.check_id == source_check_id)
    src_escalations = session.exec(stmt.order_by(OnCallEscalation.level, OnCallEscalation.id)).all()
    if not src_escalations:
        raise HTTPException(status_code=404, detail="Source template has no escalation steps")

    if payload.overwrite is not False:
        existing = session.exec(
            select(OnCallEscalation).where(OnCallEscalation.project_id == project_id, OnCallEscalation.check_id == target_check_id)
        ).all()
        for e in existing:
            session.delete(e)
        session.commit()

    # compress levels to sequential steps while preserving grouping
    level_map = {}
    ordered_levels = []
    for esc in src_escalations:
        lvl = esc.level or 0
        if lvl not in level_map:
            level_map[lvl] = len(level_map)
            ordered_levels.append(lvl)

    copied = 0
    for esc in src_escalations:
        new_level = level_map.get(esc.level or 0, 0)
        new_esc = OnCallEscalation(
            project_id=project_id,
            check_id=target_check_id,
            level=new_level,
            delay_minutes=esc.delay_minutes,
            target_type=esc.target_type,
            rotation_id=esc.rotation_id,
            target_value=esc.target_value,
            enabled=esc.enabled,
            event_types=esc.event_types,
        )
        session.add(new_esc)
        copied += 1
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        details = f"source_check_id={source_check_id} target_check_id={target_check_id} copied={copied}"
        al = AuditLog(actor=actor, action="apply_oncall_template", target_type="project", target_id=project_id, details=details, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "applied", "copied": copied}


@router.post("/escalations", status_code=status.HTTP_201_CREATED)
def create_escalation(project_id: int = Path(..., ge=1), payload: EscalationIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    if payload.check_id is not None:
        check = session.get(Check, payload.check_id)
        if not check or check.project_id != project_id:
            raise HTTPException(status_code=404, detail="Check not found for project")
    esc = OnCallEscalation(
        project_id=project_id,
        check_id=payload.check_id,
        level=payload.level,
        delay_minutes=payload.delay_minutes or 15,
        target_type=payload.target_type,
        rotation_id=payload.rotation_id,
        target_value=payload.target_value,
        enabled=payload.enabled if payload.enabled is not None else True,
        event_types=payload.event_types,
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


@router.patch("/escalations/{escalation_id}")
def update_escalation(
    project_id: int = Path(..., ge=1),
    escalation_id: int = Path(..., ge=1),
    payload: EscalationPatch = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    _rl = Depends(limit_by_api_key),
    session: Session = Depends(get_session),
):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    esc = session.get(OnCallEscalation, escalation_id)
    if not esc or esc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Escalation not found")

    data = payload.dict(exclude_unset=True)
    if "level" in data:
        esc.level = data["level"]
    if "delay_minutes" in data:
        esc.delay_minutes = data["delay_minutes"]
    if "enabled" in data:
        esc.enabled = data["enabled"]
    if "event_types" in data:
        esc.event_types = data["event_types"]
    if "target_type" in data:
        esc.target_type = data["target_type"]
    if "rotation_id" in data:
        esc.rotation_id = data["rotation_id"]
    if "target_value" in data:
        esc.target_value = data["target_value"]

    # validate target based on resulting values
    ttype = esc.target_type
    if ttype == "rotation" and not esc.rotation_id:
        raise HTTPException(status_code=400, detail="rotation_id is required when target_type=rotation")
    if ttype in ("email", "sms") and not esc.target_value:
        raise HTTPException(status_code=400, detail="target_value is required for email/sms targets")

    session.add(esc)
    session.commit()
    session.refresh(esc)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="update_oncall_escalation", target_type="oncall_escalation", target_id=esc.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
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


@router.get("/sms-settings", response_model=SmsSettingsOut)
def get_sms_settings(project_id: int = Path(..., ge=1), authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return SmsSettingsOut(
        sms_provider=project.sms_provider,
        sms_account_sid=project.sms_account_sid,
        sms_auth_token_set=bool(project.sms_auth_token),
        sms_from=project.sms_from,
    )


@router.post("/sms-settings", response_model=SmsSettingsOut)
def set_sms_settings(project_id: int = Path(..., ge=1), payload: SmsSettingsIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    require_admin_or_owner(project_id, x_admin_token=x_admin_token, authorization=authorization, session=session)
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.sms_provider is not None:
        project.sms_provider = payload.sms_provider
    if payload.sms_account_sid is not None:
        project.sms_account_sid = payload.sms_account_sid or None
    if payload.sms_auth_token is not None:
        project.sms_auth_token = payload.sms_auth_token or None
    if payload.sms_from is not None:
        project.sms_from = payload.sms_from or None
    session.add(project)
    session.commit()
    session.refresh(project)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="set_sms_settings", target_type="project", target_id=project_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return SmsSettingsOut(
        sms_provider=project.sms_provider,
        sms_account_sid=project.sms_account_sid,
        sms_auth_token_set=bool(project.sms_auth_token),
        sms_from=project.sms_from,
    )


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

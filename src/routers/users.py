from datetime import datetime, timedelta
from typing import Optional
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, status, Header, Path, Body, Request
from pydantic import BaseModel, EmailStr, constr
from sqlmodel import Session, select

from ..db import get_session
from ..models import User, UserToken, ProjectMembership, Project, AuditLog, Role
from ..security import hash_password, verify_password
from ..deps import get_current_user, get_effective_project_role, limit_public_requests, limit_auth_requests, get_audit_context, require_project_role
from ..schemas import StrictBaseModel

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserIn(StrictBaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=128)


class LoginIn(StrictBaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: Optional[datetime]


@router.post("/create", response_model=dict, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def create_user(payload: CreateUserIn, request: Request = None, x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin token required")

    existing = session.exec(select(User).where(User.email == payload.email)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    u = User(email=payload.email, hashed_password=hash_password(payload.password))
    session.add(u)
    session.commit()
    session.refresh(u)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, None, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_user", target_type="user", target_id=u.id, details=f"email={u.email}", actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"id": u.id, "email": u.email}


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def login(payload: LoginIn, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=8)
    ut = UserToken(user_id=user.id, token=token, created_at=datetime.utcnow(), expires_at=expires)
    session.add(ut)
    session.commit()
    try:
        al = AuditLog(
            actor=f"user:{user.id}",
            action="user_login",
            target_type="user",
            target_id=user.id,
            details=f"token_expires_at={expires.isoformat()}",
            actor_ip=None,
            user_agent=None,
        )
        session.add(al)
        session.commit()
    except Exception:
        pass
    return TokenOut(access_token=token, expires_at=expires)


@router.get("/me")
def me(authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = get_current_user(authorization=authorization, session=session)
    return {"id": user.id, "email": user.email}


@router.get('/projects/{project_id}/role')
def my_role(project_id: int = Path(..., ge=1), current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    role = get_effective_project_role(session, current_user.id, project_id)
    return {"role": role}


@router.get("/projects/{project_id}/membership")
def list_members(project_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # only allow project admins/owners to list members
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    members = session.exec(select(ProjectMembership).where(ProjectMembership.project_id == project_id)).all()
    out = []
    for m in members:
        u = session.get(User, m.user_id)
        out.append({"id": u.id, "email": u.email, "role": m.role})
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, None, session)
        al = AuditLog(actor=actor, action="list_project_members", target_type="project", target_id=project_id, details=f"count={len(out)}", actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return out


class MembershipIn(StrictBaseModel):
    email: EmailStr
    role: constr(regex=r"^(owner|admin|editor|viewer)$") = "viewer"


@router.post("/projects/{project_id}/membership")
def add_member(project_id: int = Path(..., ge=1), payload: MembershipIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # only admins/owners may add members
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    # find or create the invited user
    target = session.exec(select(User).where(User.email == payload.email)).first()
    if not target:
        # create a disabled user record with random password (admin will send invite externally)
        target = User(email=payload.email, hashed_password=hash_password(secrets.token_urlsafe(16)), is_active=False)
        session.add(target)
        session.commit()
        session.refresh(target)
        try:
            actor, actor_ip, user_agent = get_audit_context(request, authorization, None, session)
            al = AuditLog(actor=actor, action="invite_user", target_type="user", target_id=target.id, details=f"email={target.email}", actor_ip=actor_ip, user_agent=user_agent)
            session.add(al)
            session.commit()
        except Exception:
            pass

    # ensure membership
    existing = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == target.id, ProjectMembership.project_id == project_id)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already a member")
    membership = ProjectMembership(user_id=target.id, project_id=project_id, role=payload.role)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, None, session)
        al = AuditLog(actor=actor, action="add_project_member", target_type="project_membership", target_id=membership.id, details=f"project_id={project_id}, user_id={target.id}, role={payload.role}", actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"user_id": target.id, "email": target.email, "role": payload.role}


@router.delete("/projects/{project_id}/membership/{user_id}")
def remove_member(project_id: int = Path(..., ge=1), user_id: int = Path(..., ge=1), request: Request = None, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # only admins/owners may remove members
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    target_pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == user_id, ProjectMembership.project_id == project_id)).first()
    if not target_pm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    pm_id = target_pm.id
    session.delete(target_pm)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, None, session)
        al = AuditLog(actor=actor, action="remove_project_member", target_type="project_membership", target_id=pm_id, details=f"project_id={project_id}, user_id={user_id}", actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "removed"}

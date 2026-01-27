from datetime import datetime, timedelta
from typing import Optional
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, status, Header, Path, Body, Request
from pydantic import BaseModel, EmailStr, constr
from sqlmodel import Session, select

from ..db import get_session
from ..models import User, UserToken, ProjectMembership, Project, AuditLog
from ..security import hash_password, verify_password
from ..deps import limit_public_requests, get_audit_context
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


@router.post("/create", response_model=dict, dependencies=[Depends(limit_public_requests)])
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


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_public_requests)])
def login(payload: LoginIn, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=8)
    ut = UserToken(user_id=user.id, token=token, created_at=datetime.utcnow(), expires_at=expires)
    session.add(ut)
    session.commit()
    return TokenOut(access_token=token, expires_at=expires)


@router.get("/me")
def me(authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if ut.expires_at and ut.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    user = session.get(User, ut.user_id)
    return {"id": user.id, "email": user.email}


@router.get('/projects/{project_id}/role')
def my_role(project_id: int = Path(..., ge=1), authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # Return current user's role on the project (requires bearer token)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if ut.expires_at and ut.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    user = session.get(User, ut.user_id)
    pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)).first()
    if not pm:
        return {"role": None}
    return {"role": pm.role}


@router.get("/projects/{project_id}/membership")
def list_members(project_id: int = Path(..., ge=1), authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # only allow project owners to list members
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = session.get(User, ut.user_id)
    # ensure user is owner for the project
    pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)).first()
    if not pm or pm.role != 'owner':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")

    members = session.exec(select(ProjectMembership).where(ProjectMembership.project_id == project_id)).all()
    out = []
    for m in members:
        u = session.get(User, m.user_id)
        out.append({"id": u.id, "email": u.email, "role": m.role})
    return out


class MembershipIn(StrictBaseModel):
    email: EmailStr
    role: constr(regex=r"^(owner|viewer)$") = "viewer"


@router.post("/projects/{project_id}/membership")
def add_member(project_id: int = Path(..., ge=1), payload: MembershipIn = Body(...), request: Request = None, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # only owners may add members
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = session.get(User, ut.user_id)
    pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)).first()
    if not pm or pm.role != 'owner':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")

    # find or create the invited user
    target = session.exec(select(User).where(User.email == payload.email)).first()
    if not target:
        # create a disabled user record with random password (admin will send invite externally)
        target = User(email=payload.email, hashed_password=hash_password(secrets.token_urlsafe(16)), is_active=False)
        session.add(target)
        session.commit()
        session.refresh(target)

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
    # only owners may remove members
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = session.get(User, ut.user_id)
    pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)).first()
    if not pm or pm.role != 'owner':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")

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

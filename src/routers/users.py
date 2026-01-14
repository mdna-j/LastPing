from datetime import datetime, timedelta
from typing import Optional
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from ..db import get_session
from ..models import User, UserToken, ProjectMembership, Project
from ..security import hash_password, verify_password

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserIn(BaseModel):
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: Optional[datetime]


@router.post("/create", response_model=dict)
def create_user(payload: CreateUserIn, x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
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
    return {"id": u.id, "email": u.email}


@router.post("/login", response_model=TokenOut)
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


@router.get("/projects/{project_id}/membership")
def list_members(project_id: int, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
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

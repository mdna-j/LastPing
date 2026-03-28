from datetime import datetime
import secrets
import os
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, EmailStr, constr
from sqlmodel import Session, select

from ..db import get_session
from ..deps import authorize_org_operation, authorize_project_operation, get_audit_context, get_current_user
from ..models import (
    AuditLog,
    OrgRole,
    Organization,
    OrganizationMembership,
    Project,
    ProjectTeamAccess,
    Role,
    Team,
    TeamMembership,
    TeamRole,
    User,
)
from ..schemas import StrictBaseModel
from ..security import hash_password


router = APIRouter(prefix="/orgs", tags=["orgs"])


def _slugify(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def _audit_scope(org_id: int, *, team_id: Optional[int] = None, project_id: Optional[int] = None) -> dict:
    return {"org_id": org_id, "team_id": team_id, "project_id": project_id}


def _ensure_team_in_org(session: Session, org_id: int, team_id: int) -> Team:
    team = session.get(Team, team_id)
    if not team or team.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def _require_team_write_access(
    session: Session,
    *,
    org_id: int,
    team_id: int,
    authorization: Optional[str],
    x_admin_token: Optional[str],
) -> TeamMembership:
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token and x_admin_token and x_admin_token == admin_token:
        return TeamMembership(team_id=team_id, user_id=0, role=TeamRole.LEAD.value)
    user = get_current_user(authorization=authorization, session=session)
    org_membership = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
    ).first()
    if org_membership and org_membership.role in {OrgRole.ADMIN.value, OrgRole.OWNER.value}:
        return TeamMembership(team_id=team_id, user_id=user.id, role=TeamRole.LEAD.value)
    membership = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user.id)
    ).first()
    if not membership or membership.role != TeamRole.LEAD.value:
        raise HTTPException(status_code=403, detail="Team lead or organization admin role required")
    return membership


class OrgCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    slug: Optional[constr(min_length=1, max_length=120)] = None


class OrgRead(BaseModel):
    id: int
    name: str
    slug: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


class OrgMemberIn(StrictBaseModel):
    email: EmailStr
    role: constr(regex=r"^(owner|admin|member)$") = OrgRole.MEMBER.value


class OrgMemberRead(BaseModel):
    user_id: int
    email: str
    role: str


class TeamCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    slug: Optional[constr(min_length=1, max_length=120)] = None


class TeamRead(BaseModel):
    id: int
    organization_id: int
    name: str
    slug: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


class TeamMemberIn(StrictBaseModel):
    email: EmailStr
    role: constr(regex=r"^(lead|member)$") = TeamRole.MEMBER.value


class TeamProjectAccessIn(StrictBaseModel):
    role: constr(regex=r"^(owner|admin|editor|viewer)$") = Role.VIEWER.value


@router.post("/", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
def create_org(
    payload: OrgCreate,
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    admin_token = os.environ.get("ADMIN_TOKEN")
    current_user = None
    if isinstance(authorization, str) and authorization:
        current_user = get_current_user(authorization=authorization, session=session)
    elif not (admin_token and x_admin_token and x_admin_token == admin_token):
        raise HTTPException(status_code=401, detail="Missing credentials")

    slug = payload.slug or _slugify(payload.name)
    org = Organization(name=payload.name, slug=slug or None)
    session.add(org)
    session.commit()
    session.refresh(org)
    if current_user:
        session.add(
            OrganizationMembership(
                organization_id=org.id,
                user_id=current_user.id,
                role=OrgRole.OWNER.value,
            )
        )
        session.commit()
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="create_org",
            target_type="organization",
            target_id=org.id,
            details=f"slug={slug}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org.id),
        )
    )
    session.commit()
    return OrgRead.from_orm(org)


@router.get("/mine", response_model=List[OrgRead])
def list_my_orgs(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    stmt = (
        select(Organization)
        .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
        .where(OrganizationMembership.user_id == current_user.id)
        .order_by(Organization.name)
    )
    return [OrgRead.from_orm(row) for row in session.exec(stmt).all()]


@router.get("/{org_id}/members", response_model=List[OrgMemberRead])
def list_org_members(
    org_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.MEMBER.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    memberships = session.exec(select(OrganizationMembership).where(OrganizationMembership.organization_id == org_id)).all()
    out: List[OrgMemberRead] = []
    for membership in memberships:
        user = session.get(User, membership.user_id)
        out.append(OrgMemberRead(user_id=membership.user_id, email=user.email if user else "unknown", role=membership.role))
    return out


@router.post("/{org_id}/members", response_model=OrgMemberRead)
def add_org_member(
    org_id: int = Path(..., ge=1),
    payload: OrgMemberIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user:
        user = User(email=payload.email, hashed_password=hash_password(secrets.token_urlsafe(16)), is_active=False)
        session.add(user)
        session.commit()
        session.refresh(user)
    existing = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
    ).first()
    if existing:
        existing.role = payload.role
        membership = existing
    else:
        membership = OrganizationMembership(organization_id=org_id, user_id=user.id, role=payload.role)
    session.add(membership)
    session.commit()
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="upsert_org_member",
            target_type="organization_membership",
            target_id=membership.id,
            details=f"user_id={user.id}, role={payload.role}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id),
        )
    )
    session.commit()
    return OrgMemberRead(user_id=user.id, email=user.email, role=membership.role)


@router.get("/{org_id}/teams", response_model=List[TeamRead])
def list_org_teams(
    org_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.MEMBER.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    rows = session.exec(select(Team).where(Team.organization_id == org_id).order_by(Team.name)).all()
    return [TeamRead.from_orm(row) for row in rows]


@router.post("/{org_id}/teams", response_model=TeamRead, status_code=status.HTTP_201_CREATED)
def create_team(
    org_id: int = Path(..., ge=1),
    payload: TeamCreate = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    team = Team(organization_id=org_id, name=payload.name, slug=payload.slug or _slugify(payload.name) or None)
    session.add(team)
    session.commit()
    session.refresh(team)
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="create_team",
            target_type="team",
            target_id=team.id,
            details=f"name={team.name}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, team_id=team.id),
        )
    )
    session.commit()
    return TeamRead.from_orm(team)


@router.post("/{org_id}/teams/{team_id}/members", status_code=status.HTTP_200_OK)
def add_team_member(
    org_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    payload: TeamMemberIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    _ensure_team_in_org(session, org_id, team_id)
    _require_team_write_access(session, org_id=org_id, team_id=team_id, authorization=authorization, x_admin_token=x_admin_token)
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user:
        user = User(email=payload.email, hashed_password=hash_password(secrets.token_urlsafe(16)), is_active=False)
        session.add(user)
        session.commit()
        session.refresh(user)
    existing = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user.id)
    ).first()
    if existing:
        existing.role = payload.role
        membership = existing
    else:
        membership = TeamMembership(team_id=team_id, user_id=user.id, role=payload.role)
    session.add(membership)
    session.commit()
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="upsert_team_member",
            target_type="team_membership",
            target_id=membership.id,
            details=f"user_id={user.id}, role={payload.role}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, team_id=team_id),
        )
    )
    session.commit()
    return {"team_id": team_id, "user_id": user.id, "email": user.email, "role": membership.role}


@router.post("/{org_id}/projects/{project_id}/attach", response_model=dict)
def attach_project_to_org(
    org_id: int = Path(..., ge=1),
    project_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    project = authorize_project_operation(
        project_id,
        min_role=Role.ADMIN.value,
        authorization=authorization,
        x_admin_token=x_admin_token,
        x_api_key=x_api_key,
        session=session,
    )
    if project.org_id and project.org_id != org_id:
        raise HTTPException(status_code=400, detail="Project already belongs to another organization")
    project.org_id = org_id
    session.add(project)
    session.commit()
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="attach_project_to_org",
            target_type="project",
            target_id=project_id,
            details=None,
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, project_id=project_id),
        )
    )
    session.commit()
    return {"project_id": project_id, "org_id": org_id}


@router.post("/{org_id}/teams/{team_id}/projects/{project_id}", response_model=dict)
def grant_team_project_access(
    org_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    project_id: int = Path(..., ge=1),
    payload: TeamProjectAccessIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    _ensure_team_in_org(session, org_id, team_id)
    project = session.get(Project, project_id)
    if not project or project.org_id != org_id:
        raise HTTPException(status_code=404, detail="Project not found in organization")
    access = session.exec(
        select(ProjectTeamAccess).where(ProjectTeamAccess.project_id == project_id, ProjectTeamAccess.team_id == team_id)
    ).first()
    if access:
        access.role = payload.role
    else:
        access = ProjectTeamAccess(project_id=project_id, team_id=team_id, role=payload.role)
    session.add(access)
    session.commit()
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action="grant_team_project_access",
            target_type="project_team_access",
            target_id=access.id,
            details=f"role={payload.role}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, team_id=team_id, project_id=project_id),
        )
    )
    session.commit()
    return {"project_id": project_id, "team_id": team_id, "role": access.role}

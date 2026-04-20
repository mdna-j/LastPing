from __future__ import annotations

from datetime import datetime
import secrets
import os
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, EmailStr, constr, root_validator
from sqlmodel import Session, select

from ..db import get_session
from ..deps import authorize_org_operation, authorize_project_operation, get_audit_context, get_current_user
from ..models import (
    ApiKey,
    AuditLog,
    OrgRole,
    Organization,
    OrganizationGroupMapping,
    OrganizationMembership,
    Project,
    ProjectTeamAccess,
    Role,
    Team,
    TeamGroupMapping,
    TeamMembership,
    TeamRole,
    User,
)
from ..schemas import StrictBaseModel
from ..security import generate_api_key, hash_api_key, hash_password
from ..secret_lifecycle import api_key_rotation_due_at, api_key_rotation_required


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


def _record_org_audit(
    session: Session,
    *,
    request: Optional[Request],
    authorization: Optional[str],
    x_admin_token: Optional[str],
    org_id: int,
    action: str,
    target_type: str,
    target_id: Optional[int],
    details: Optional[str],
    team_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> None:
    actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, team_id=team_id, project_id=project_id),
        )
    )
    session.commit()


def _serialize_group_mapping(mapping: OrganizationGroupMapping | TeamGroupMapping, *, scope: str) -> GroupMappingRead:
    return GroupMappingRead(
        id=mapping.id,
        scope=scope,
        organization_id=mapping.organization_id,
        team_id=getattr(mapping, "team_id", None),
        provider=mapping.provider,
        external_group=mapping.external_group,
        role=mapping.role,
        created_at=mapping.created_at,
    )


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


class TeamMemberRead(BaseModel):
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


class ScimSettingsRead(BaseModel):
    configured: bool
    last_rotated_at: Optional[datetime]


class ScimRotateRead(ScimSettingsRead):
    bearer_token: str


class OrgGroupMappingIn(StrictBaseModel):
    provider: constr(min_length=1, max_length=80)
    external_group: constr(min_length=1, max_length=255)
    role: constr(regex=r"^(owner|admin|member)$") = OrgRole.MEMBER.value


class TeamGroupMappingIn(StrictBaseModel):
    provider: constr(min_length=1, max_length=80)
    external_group: constr(min_length=1, max_length=255)
    role: constr(regex=r"^(lead|member)$") = TeamRole.MEMBER.value


class GroupMappingRead(BaseModel):
    id: int
    scope: str
    organization_id: int
    team_id: Optional[int] = None
    provider: str
    external_group: str
    role: str
    created_at: datetime


class OrgOverviewItemRead(BaseModel):
    organization_id: int
    organization_name: str
    slug: Optional[str] = None
    role: str
    team_count: int
    project_count: int
    owner_project_count: int
    service_account_count: int


class OwnerTeamUpdate(StrictBaseModel):
    team_id: int


class ServiceAccountCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    description: Optional[constr(max_length=240)] = None
    role: constr(regex=r"^(owner|admin|editor|viewer)$") = Role.EDITOR.value
    team_id: Optional[int] = None
    rate_limit_per_minute: Optional[int] = 0
    expires_at: Optional[datetime] = None
    rotation_interval_days: Optional[int] = None

    @root_validator
    def _validate_lifecycle(cls, values):
        expires_at = values.get("expires_at")
        rotation_interval_days = values.get("rotation_interval_days")
        if expires_at and expires_at <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        if rotation_interval_days is not None and int(rotation_interval_days) <= 0:
            raise ValueError("rotation_interval_days must be positive")
        return values


def _clear_managed_membership_fields(membership: OrganizationMembership | TeamMembership) -> None:
    membership.managed_provider = None
    membership.managed_group = None
    membership.managed_fallback_role = None
    membership.managed_last_synced_at = None


def _count_org_owners(session: Session, org_id: int) -> int:
    return len(
        session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.role == OrgRole.OWNER.value,
            )
        ).all()
    )


def _serialize_team_summary(session: Session, team: Team, *, access_rows: list[ProjectTeamAccess]) -> dict:
    member_count = len(
        session.exec(select(TeamMembership).where(TeamMembership.team_id == team.id)).all()
    )
    owned_project_count = sum(1 for row in access_rows if row.team_id == team.id and row.role == Role.OWNER.value)
    accessible_project_count = sum(1 for row in access_rows if row.team_id == team.id)
    return {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "member_count": member_count,
        "owned_project_count": owned_project_count,
        "accessible_project_count": accessible_project_count,
    }


def _serialize_token_inventory_row(
    token: ApiKey,
    *,
    project: Project,
    team: Optional[Team],
    creator: Optional[User],
) -> dict:
    return {
        "id": token.id,
        "project_id": token.project_id,
        "project_name": project.name,
        "name": token.name,
        "description": getattr(token, "description", None),
        "role": token.role,
        "token_type": getattr(token, "token_type", "project_token"),
        "managed_by_team_id": getattr(token, "managed_by_team_id", None),
        "managed_by_team_name": team.name if team else None,
        "is_active": token.is_active,
        "revoked_at": token.revoked_at,
        "last_used_at": token.last_used_at,
        "expires_at": token.expires_at,
        "last_rotated_at": token.last_rotated_at or token.created_at,
        "rotation_interval_days": token.rotation_interval_days,
        "rotation_due_at": api_key_rotation_due_at(token),
        "rotation_required": api_key_rotation_required(token),
        "created_by_user_id": token.created_by_user_id,
        "created_by_email": creator.email if creator else None,
        "created_at": token.created_at,
        "is_primary": bool(project.api_key_hash and token.key_hash == project.api_key_hash),
    }


def _org_projects(session: Session, org_id: int) -> list[Project]:
    return session.exec(select(Project).where(Project.org_id == org_id).order_by(Project.name)).all()


def _org_access_rows(session: Session, org_id: int) -> list[ProjectTeamAccess]:
    project_ids = [project.id for project in _org_projects(session, org_id) if project.id is not None]
    if not project_ids:
        return []
    return session.exec(
        select(ProjectTeamAccess).where(ProjectTeamAccess.project_id.in_(project_ids))
    ).all()


def _org_token_rows(session: Session, org_id: int) -> list[ApiKey]:
    project_ids = [project.id for project in _org_projects(session, org_id) if project.id is not None]
    if not project_ids:
        return []
    return session.exec(
        select(ApiKey).where(ApiKey.project_id.in_(project_ids)).order_by(ApiKey.created_at.desc())
    ).all()


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


@router.get("/mine/overview", response_model=List[OrgOverviewItemRead])
def list_my_org_overview(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    memberships = session.exec(
        select(OrganizationMembership).where(OrganizationMembership.user_id == current_user.id)
    ).all()
    rows: List[OrgOverviewItemRead] = []
    for membership in memberships:
        org = session.get(Organization, membership.organization_id)
        if not org:
            continue
        projects = _org_projects(session, org.id)
        access_rows = _org_access_rows(session, org.id)
        tokens = _org_token_rows(session, org.id)
        rows.append(
            OrgOverviewItemRead(
                organization_id=org.id,
                organization_name=org.name,
                slug=org.slug,
                role=membership.role,
                team_count=len(session.exec(select(Team).where(Team.organization_id == org.id)).all()),
                project_count=len(projects),
                owner_project_count=len({row.project_id for row in access_rows if row.role == Role.OWNER.value}),
                service_account_count=sum(
                    1 for token in tokens if getattr(token, "token_type", "project_token") == "service_account"
                ),
            )
        )
    rows.sort(key=lambda row: row.organization_name.lower())
    return rows


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
    out.sort(key=lambda row: row.email.lower())
    return out


@router.get("/{org_id}/overview", response_model=dict)
def get_org_overview(
    org_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    membership = authorize_org_operation(
        org_id,
        min_role=OrgRole.MEMBER.value,
        authorization=authorization,
        x_admin_token=x_admin_token,
        session=session,
    )
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    teams = session.exec(select(Team).where(Team.organization_id == org_id).order_by(Team.name)).all()
    projects = _org_projects(session, org_id)
    access_rows = _org_access_rows(session, org_id)
    tokens = _org_token_rows(session, org_id)
    team_by_id = {team.id: team for team in teams}
    access_by_project: dict[int, list[ProjectTeamAccess]] = {}
    for row in access_rows:
        access_by_project.setdefault(row.project_id, []).append(row)
    tokens_by_project: dict[int, list[ApiKey]] = {}
    for token in tokens:
        tokens_by_project.setdefault(token.project_id, []).append(token)

    return {
        "organization": {
            "id": org.id,
            "name": org.name,
            "slug": org.slug,
            "created_at": org.created_at,
        },
        "current_role": membership.role,
        "summary": {
            "team_count": len(teams),
            "project_count": len(projects),
            "owner_team_count": len({row.team_id for row in access_rows if row.role == Role.OWNER.value}),
            "service_account_count": sum(
                1 for token in tokens if getattr(token, "token_type", "project_token") == "service_account"
            ),
        },
        "teams": [_serialize_team_summary(session, team, access_rows=access_rows) for team in teams],
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "created_at": project.created_at,
                "owner_teams": [
                    {
                        "id": row.team_id,
                        "name": team_by_id[row.team_id].name if row.team_id in team_by_id else f"Team {row.team_id}",
                        "role": row.role,
                    }
                    for row in access_by_project.get(project.id, [])
                    if row.role == Role.OWNER.value
                ],
                "accessible_teams": [
                    {
                        "id": row.team_id,
                        "name": team_by_id[row.team_id].name if row.team_id in team_by_id else f"Team {row.team_id}",
                        "role": row.role,
                    }
                    for row in access_by_project.get(project.id, [])
                ],
                "service_account_count": sum(
                    1
                    for token in tokens_by_project.get(project.id, [])
                    if getattr(token, "token_type", "project_token") == "service_account"
                ),
                "active_token_count": sum(
                    1 for token in tokens_by_project.get(project.id, []) if token.is_active and token.revoked_at is None
                ),
            }
            for project in projects
        ],
    }


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
        if (
            existing.role == OrgRole.OWNER.value
            and payload.role != OrgRole.OWNER.value
            and _count_org_owners(session, org_id) <= 1
        ):
            raise HTTPException(status_code=400, detail="Cannot remove the last organization owner")
        existing.role = payload.role
        _clear_managed_membership_fields(existing)
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


@router.delete("/{org_id}/members/{user_id}", response_model=dict)
def remove_org_member(
    org_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    membership = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Organization member not found")
    if membership.role == OrgRole.OWNER.value and _count_org_owners(session, org_id) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last organization owner")

    team_ids = [team.id for team in session.exec(select(Team).where(Team.organization_id == org_id)).all()]
    team_memberships = []
    if team_ids:
        team_memberships = session.exec(
            select(TeamMembership).where(TeamMembership.user_id == user_id, TeamMembership.team_id.in_(team_ids))
        ).all()
    removed_team_ids = [row.team_id for row in team_memberships]
    membership_id = membership.id
    for row in team_memberships:
        session.delete(row)
    session.delete(membership)
    session.commit()

    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="remove_org_member",
        target_type="organization_membership",
        target_id=membership_id,
        details=f"user_id={user_id}, removed_team_ids={removed_team_ids}",
    )
    return {"removed": True, "user_id": user_id, "removed_team_memberships": len(removed_team_ids)}


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
    org_membership = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
    ).first()
    org_membership_created = False
    if not org_membership:
        org_membership = OrganizationMembership(organization_id=org_id, user_id=user.id, role=OrgRole.MEMBER.value)
        session.add(org_membership)
        session.commit()
        session.refresh(org_membership)
        org_membership_created = True
    existing = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user.id)
    ).first()
    if existing:
        existing.role = payload.role
        _clear_managed_membership_fields(existing)
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
            details=f"user_id={user.id}, role={payload.role}, org_membership_created={org_membership_created}",
            actor_ip=actor_ip,
            user_agent=user_agent,
            **_audit_scope(org_id, team_id=team_id),
        )
    )
    session.commit()
    return {"team_id": team_id, "user_id": user.id, "email": user.email, "role": membership.role}


@router.get("/{org_id}/teams/{team_id}/members", response_model=List[TeamMemberRead])
def list_team_members(
    org_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.MEMBER.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    _ensure_team_in_org(session, org_id, team_id)
    memberships = session.exec(select(TeamMembership).where(TeamMembership.team_id == team_id)).all()
    out: List[TeamMemberRead] = []
    for membership in memberships:
        user = session.get(User, membership.user_id)
        out.append(TeamMemberRead(user_id=membership.user_id, email=user.email if user else "unknown", role=membership.role))
    out.sort(key=lambda row: row.email.lower())
    return out


@router.delete("/{org_id}/teams/{team_id}/members/{user_id}", response_model=dict)
def remove_team_member(
    org_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    _ensure_team_in_org(session, org_id, team_id)
    _require_team_write_access(session, org_id=org_id, team_id=team_id, authorization=authorization, x_admin_token=x_admin_token)
    membership = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Team member not found")
    membership_id = membership.id
    session.delete(membership)
    session.commit()
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="remove_team_member",
        target_type="team_membership",
        target_id=membership_id,
        details=f"user_id={user_id}",
        team_id=team_id,
    )
    return {"removed": True, "team_id": team_id, "user_id": user_id}


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


@router.put("/{org_id}/projects/{project_id}/owner-team", response_model=dict)
def set_project_owner_team(
    org_id: int = Path(..., ge=1),
    project_id: int = Path(..., ge=1),
    payload: OwnerTeamUpdate = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    team = _ensure_team_in_org(session, org_id, payload.team_id)
    project = session.get(Project, project_id)
    if not project or project.org_id != org_id:
        raise HTTPException(status_code=404, detail="Project not found in organization")

    existing_rows = session.exec(
        select(ProjectTeamAccess).where(ProjectTeamAccess.project_id == project_id)
    ).all()
    previous_owner_team_ids = [row.team_id for row in existing_rows if row.role == Role.OWNER.value]
    for row in existing_rows:
        if row.role == Role.OWNER.value and row.team_id != team.id:
            session.delete(row)

    owner_row = next((row for row in existing_rows if row.team_id == team.id), None)
    if owner_row is None:
        owner_row = ProjectTeamAccess(project_id=project_id, team_id=team.id, role=Role.OWNER.value)
    else:
        owner_row.role = Role.OWNER.value
    session.add(owner_row)
    session.commit()
    session.refresh(owner_row)

    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="set_project_owner_team",
        target_type="project_team_access",
        target_id=owner_row.id,
        details=f"project_id={project_id}, previous_owner_team_ids={previous_owner_team_ids}, new_owner_team_id={team.id}",
        team_id=team.id,
        project_id=project_id,
    )
    return {"project_id": project_id, "team_id": team.id, "team_name": team.name, "role": owner_row.role}


@router.get("/{org_id}/token-inventory", response_model=dict)
def get_org_token_inventory(
    org_id: int = Path(..., ge=1),
    token_type: Optional[str] = Query(None, max_length=40),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    projects = _org_projects(session, org_id)
    project_by_id = {project.id: project for project in projects}
    team_by_id = {
        team.id: team for team in session.exec(select(Team).where(Team.organization_id == org_id)).all()
    }
    tokens = _org_token_rows(session, org_id)
    if token_type:
        tokens = [token for token in tokens if getattr(token, "token_type", "project_token") == token_type]
    creator_ids = [token.created_by_user_id for token in tokens if token.created_by_user_id is not None]
    creators = {}
    if creator_ids:
        for user in session.exec(select(User).where(User.id.in_(creator_ids))).all():
            creators[user.id] = user
    rows = [
        _serialize_token_inventory_row(
            token,
            project=project_by_id[token.project_id],
            team=team_by_id.get(getattr(token, "managed_by_team_id", None)),
            creator=creators.get(token.created_by_user_id),
        )
        for token in tokens
        if token.project_id in project_by_id
    ]
    return {
        "organization": {"id": org.id, "name": org.name},
        "summary": {
            "token_count": len(rows),
            "service_account_count": sum(1 for row in rows if row["token_type"] == "service_account"),
            "expiring_count": sum(1 for row in rows if row["expires_at"] is not None and row["revoked_at"] is None),
            "active_count": sum(1 for row in rows if row["is_active"] and row["revoked_at"] is None),
        },
        "tokens": rows,
    }


@router.post("/{org_id}/projects/{project_id}/service-accounts", status_code=status.HTTP_201_CREATED)
def create_service_account(
    org_id: int = Path(..., ge=1),
    project_id: int = Path(..., ge=1),
    payload: ServiceAccountCreate = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    project = session.get(Project, project_id)
    if not project or project.org_id != org_id:
        raise HTTPException(status_code=404, detail="Project not found in organization")
    team = None
    if payload.team_id is not None:
        team = _ensure_team_in_org(session, org_id, payload.team_id)

    creator = None
    if isinstance(authorization, str) and authorization:
        try:
            creator = get_current_user(authorization=authorization, session=session)
        except HTTPException:
            creator = None

    plain = generate_api_key()
    token = ApiKey(
        project_id=project_id,
        key_hash=hash_api_key(plain),
        name=payload.name,
        description=payload.description,
        role=payload.role,
        token_type="service_account",
        managed_by_team_id=payload.team_id,
        rate_limit_per_minute=payload.rate_limit_per_minute or 0,
        created_by_user_id=creator.id if creator else None,
        expires_at=payload.expires_at,
        rotation_interval_days=payload.rotation_interval_days,
        last_rotated_at=datetime.utcnow(),
    )
    session.add(token)
    session.commit()
    session.refresh(token)

    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="create_service_account_token",
        target_type="api_key",
        target_id=token.id,
        details=f"project_id={project_id}, role={payload.role}, managed_by_team_id={payload.team_id}",
        team_id=payload.team_id,
        project_id=project_id,
    )
    return {
        "api_key": plain,
        "token": _serialize_token_inventory_row(
            token,
            project=project,
            team=team,
            creator=creator,
        ),
    }


@router.post("/{org_id}/tokens/{api_key_id}/revoke", response_model=dict)
def revoke_org_token(
    org_id: int = Path(..., ge=1),
    api_key_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    token = session.get(ApiKey, api_key_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    project = session.get(Project, token.project_id)
    if not project or project.org_id != org_id:
        raise HTTPException(status_code=404, detail="Token not found in organization")
    if project.api_key_hash and token.key_hash == project.api_key_hash:
        raise HTTPException(status_code=400, detail="Use project key rotation for the primary token")
    token.is_active = False
    token.revoked_at = datetime.utcnow()
    session.add(token)
    session.commit()
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="revoke_org_token",
        target_type="api_key",
        target_id=token.id,
        details=f"project_id={project.id}, token_type={getattr(token, 'token_type', 'project_token')}",
        team_id=getattr(token, "managed_by_team_id", None),
        project_id=project.id,
    )
    return {"revoked": True, "api_key_id": token.id}


@router.get("/{org_id}/membership-audit", response_model=dict)
def get_membership_audit_history(
    org_id: int = Path(..., ge=1),
    limit: int = Query(50, ge=1, le=200),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    actions = {
        "upsert_org_member",
        "upsert_team_member",
        "remove_org_member",
        "remove_team_member",
        "attach_project_to_org",
        "grant_team_project_access",
        "set_project_owner_team",
        "create_service_account_token",
        "revoke_org_token",
        "upsert_org_group_mapping",
        "upsert_team_group_mapping",
        "delete_org_group_mapping",
        "delete_team_group_mapping",
    }
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action.in_(actions))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "actor": row.actor,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "team_id": row.team_id,
                "project_id": row.project_id,
                "details": row.details,
            }
            for row in rows
        ]
    }


@router.get("/{org_id}/scim-settings", response_model=ScimSettingsRead)
def get_scim_settings(
    org_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return ScimSettingsRead(
        configured=bool((org.scim_bearer_token or "").strip()),
        last_rotated_at=org.scim_last_rotated_at,
    )


@router.post("/{org_id}/scim-settings/rotate", response_model=ScimRotateRead)
def rotate_scim_bearer_token(
    org_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    bearer_token = f"lp_scim_{secrets.token_urlsafe(32)}"
    org.scim_bearer_token = bearer_token
    org.scim_last_rotated_at = datetime.utcnow()
    session.add(org)
    session.commit()
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="rotate_scim_bearer_token",
        target_type="organization",
        target_id=org.id,
        details="rotated organization SCIM bearer token",
    )
    return ScimRotateRead(
        configured=True,
        last_rotated_at=org.scim_last_rotated_at,
        bearer_token=bearer_token,
    )


@router.get("/{org_id}/group-mappings", response_model=List[GroupMappingRead])
def list_group_mappings(
    org_id: int = Path(..., ge=1),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    org_mappings = session.exec(
        select(OrganizationGroupMapping).where(OrganizationGroupMapping.organization_id == org_id)
    ).all()
    team_mappings = session.exec(
        select(TeamGroupMapping).where(TeamGroupMapping.organization_id == org_id)
    ).all()
    rows = [_serialize_group_mapping(mapping, scope="organization") for mapping in org_mappings]
    rows.extend(_serialize_group_mapping(mapping, scope="team") for mapping in team_mappings)
    rows.sort(key=lambda row: (row.scope, row.provider.lower(), row.external_group.lower(), row.team_id or 0))
    return rows


@router.post("/{org_id}/group-mappings/org", response_model=GroupMappingRead)
def upsert_org_group_mapping(
    org_id: int = Path(..., ge=1),
    payload: OrgGroupMappingIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    mapping = session.exec(
        select(OrganizationGroupMapping).where(
            OrganizationGroupMapping.organization_id == org_id,
            OrganizationGroupMapping.provider == payload.provider,
            OrganizationGroupMapping.external_group == payload.external_group,
        )
    ).first()
    if mapping is None:
        mapping = OrganizationGroupMapping(
            organization_id=org_id,
            provider=payload.provider,
            external_group=payload.external_group,
            role=payload.role,
        )
    else:
        mapping.role = payload.role
    session.add(mapping)
    session.commit()
    session.refresh(mapping)
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="upsert_org_group_mapping",
        target_type="organization_group_mapping",
        target_id=mapping.id,
        details=f"provider={mapping.provider}, external_group={mapping.external_group}, role={mapping.role}",
    )
    return _serialize_group_mapping(mapping, scope="organization")


@router.post("/{org_id}/group-mappings/team/{team_id}", response_model=GroupMappingRead)
def upsert_team_group_mapping(
    org_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    payload: TeamGroupMappingIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    _ensure_team_in_org(session, org_id, team_id)
    mapping = session.exec(
        select(TeamGroupMapping).where(
            TeamGroupMapping.organization_id == org_id,
            TeamGroupMapping.team_id == team_id,
            TeamGroupMapping.provider == payload.provider,
            TeamGroupMapping.external_group == payload.external_group,
        )
    ).first()
    if mapping is None:
        mapping = TeamGroupMapping(
            organization_id=org_id,
            team_id=team_id,
            provider=payload.provider,
            external_group=payload.external_group,
            role=payload.role,
        )
    else:
        mapping.role = payload.role
    session.add(mapping)
    session.commit()
    session.refresh(mapping)
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="upsert_team_group_mapping",
        target_type="team_group_mapping",
        target_id=mapping.id,
        details=f"provider={mapping.provider}, external_group={mapping.external_group}, role={mapping.role}",
        team_id=team_id,
    )
    return _serialize_group_mapping(mapping, scope="team")


@router.delete("/{org_id}/group-mappings/org/{mapping_id}", response_model=dict)
def delete_org_group_mapping(
    org_id: int = Path(..., ge=1),
    mapping_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    mapping = session.get(OrganizationGroupMapping, mapping_id)
    if not mapping or mapping.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Group mapping not found")
    session.delete(mapping)
    session.commit()
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="delete_org_group_mapping",
        target_type="organization_group_mapping",
        target_id=mapping_id,
        details=None,
    )
    return {"deleted": True}


@router.delete("/{org_id}/group-mappings/team/{mapping_id}", response_model=dict)
def delete_team_group_mapping(
    org_id: int = Path(..., ge=1),
    mapping_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    authorize_org_operation(org_id, min_role=OrgRole.ADMIN.value, authorization=authorization, x_admin_token=x_admin_token, session=session)
    mapping = session.get(TeamGroupMapping, mapping_id)
    if not mapping or mapping.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Group mapping not found")
    session.delete(mapping)
    session.commit()
    _record_org_audit(
        session,
        request=request,
        authorization=authorization,
        x_admin_token=x_admin_token,
        org_id=org_id,
        action="delete_team_group_mapping",
        target_type="team_group_mapping",
        target_id=mapping_id,
        details=None,
        team_id=mapping.team_id,
    )
    return {"deleted": True}

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, status
from sqlmodel import Session, select

from ..db import get_session
from ..identity_sync import sync_identity_groups
from ..models import AuditLog, OrgRole, Organization, OrganizationMembership, Team, TeamGroupMapping, TeamMembership, TeamRole, User, UserIdentity
from ..security import hash_password

router = APIRouter(prefix="/scim/v2", tags=["scim"])

SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LASTPING_GROUP_SCHEMA = "urn:lastping:schemas:scim:group:1.0"


def _slugify(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization.split(None, 1)[1].strip()
    return None


def _get_scim_org(
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
) -> Organization:
    token = _parse_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing SCIM bearer token")
    organizations = session.exec(select(Organization)).all()
    for org in organizations:
        configured = (org.scim_bearer_token or "").strip()
        if configured and secrets.compare_digest(configured, token):
            return org
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid SCIM bearer token")


def _scim_subject(org_id: int, payload: dict[str, Any], *, fallback_email: str) -> str:
    external_id = str(payload.get("externalId") or "").strip()
    basis = external_id or fallback_email
    return f"{org_id}:{basis.lower()}"


def _extract_email(payload: dict[str, Any]) -> str:
    if payload.get("userName"):
        return str(payload["userName"]).strip().lower()
    emails = payload.get("emails")
    if isinstance(emails, list):
        for row in emails:
            if isinstance(row, dict) and row.get("value"):
                return str(row["value"]).strip().lower()
            if isinstance(row, str) and row.strip():
                return row.strip().lower()
    raise HTTPException(status_code=400, detail="SCIM user payload is missing userName/email")


def _extract_display_name(payload: dict[str, Any], *, fallback_email: str) -> str:
    if payload.get("displayName"):
        return str(payload["displayName"]).strip()
    name = payload.get("name")
    if isinstance(name, dict):
        formatted = str(name.get("formatted") or "").strip()
        if formatted:
            return formatted
        given = str(name.get("givenName") or "").strip()
        family = str(name.get("familyName") or "").strip()
        joined = " ".join(part for part in [given, family] if part)
        if joined:
            return joined
    return fallback_email


def _extract_groups(payload: dict[str, Any]) -> list[str]:
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        return []
    groups: list[str] = []
    seen: set[str] = set()
    for item in raw_groups:
        if isinstance(item, dict):
            value = str(item.get("display") or item.get("value") or "").strip()
        else:
            value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        groups.append(value)
    groups.sort(key=str.lower)
    return groups


def _identity_groups(identity: Optional[UserIdentity]) -> list[str]:
    if identity is None or not identity.last_groups_json:
        return []
    try:
        payload = json.loads(identity.last_groups_json)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item or "").strip()]


def _scim_identities_for_org(session: Session, org: Organization) -> list[UserIdentity]:
    return session.exec(
        select(UserIdentity).where(
            UserIdentity.provider == "scim",
            UserIdentity.provider_subject.like(f"{org.id}:%"),
        )
    ).all()


def _groups_without(group_names: list[str], target: str) -> list[str]:
    lowered = target.strip().lower()
    return [group for group in group_names if group.strip().lower() != lowered]


def _groups_with(group_names: list[str], target: str) -> list[str]:
    values = _groups_without(group_names, target)
    values.append(target)
    values.sort(key=str.lower)
    return values


def _group_member_rows(session: Session, *, org: Organization, external_group: str) -> list[tuple[User, UserIdentity]]:
    rows: list[tuple[User, UserIdentity]] = []
    lowered = external_group.strip().lower()
    for identity in _scim_identities_for_org(session, org):
        groups = {group.strip().lower() for group in _identity_groups(identity)}
        if lowered not in groups:
            continue
        user = session.get(User, identity.user_id)
        if user is None:
            continue
        rows.append((user, identity))
    rows.sort(key=lambda row: (row[0].email or "").lower())
    return rows


def _resolve_scim_group_mapping(session: Session, *, org: Organization, group_id: int) -> TeamGroupMapping:
    mapping = session.get(TeamGroupMapping, group_id)
    if not mapping or mapping.organization_id != org.id or mapping.provider != "scim":
        raise HTTPException(status_code=404, detail="SCIM group not found")
    return mapping


def _serialize_group_resource(session: Session, *, org: Organization, mapping: TeamGroupMapping) -> dict[str, Any]:
    team = session.get(Team, mapping.team_id)
    members = [
        {
            "value": str(user.id),
            "$ref": f"/scim/v2/Users/{user.id}",
            "display": user.display_name or user.email,
        }
        for user, _identity in _group_member_rows(session, org=org, external_group=mapping.external_group)
    ]
    return {
        "schemas": [SCIM_GROUP_SCHEMA, LASTPING_GROUP_SCHEMA],
        "id": str(mapping.id),
        "displayName": mapping.external_group,
        "members": members,
        LASTPING_GROUP_SCHEMA: {
            "teamId": mapping.team_id,
            "teamName": team.name if team else None,
            "role": mapping.role,
        },
        "meta": {
            "resourceType": "Group",
            "created": mapping.created_at.isoformat() if mapping.created_at else None,
            "organizationId": org.id,
        },
    }


def _serialize_user_resource(
    *,
    org: Organization,
    user: User,
    identity: Optional[UserIdentity],
    active: bool,
) -> dict[str, Any]:
    groups = [{"display": item, "value": item} for item in _identity_groups(identity)]
    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": str(user.id),
        "externalId": identity.provider_subject.split(":", 1)[1] if identity and ":" in identity.provider_subject else None,
        "userName": user.email,
        "displayName": user.display_name or user.email,
        "active": active,
        "emails": [{"value": user.email, "primary": True}],
        "groups": groups,
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat() if user.created_at else None,
            "organizationId": org.id,
        },
    }


def _record_scim_audit(
    session: Session,
    *,
    org: Organization,
    action: str,
    target_type: str = "user",
    target_id: Optional[int],
    details: Optional[str],
) -> None:
    session.add(
        AuditLog(
            actor=f"scim:org:{org.id}",
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            org_id=org.id,
        )
    )
    session.commit()


def _deprovision_scim_access(
    session: Session,
    *,
    org: Organization,
    user: User,
    identity: Optional[UserIdentity],
    occurred_at: datetime,
    remove_identity: bool,
) -> None:
    _ensure_scim_membership(session, org=org, user=user, active=False, occurred_at=occurred_at)
    if identity is None:
        return
    if remove_identity:
        session.delete(identity)
    else:
        identity.last_groups_json = "[]"
        session.add(identity)


def _membership_for_org(session: Session, *, org_id: int, user_id: int) -> Optional[OrganizationMembership]:
    return session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
    ).first()


def _upsert_scim_identity(
    session: Session,
    *,
    org: Organization,
    user: User,
    payload: dict[str, Any],
    display_name: str,
    email: str,
    occurred_at: datetime,
) -> UserIdentity:
    subject = _scim_subject(org.id, payload, fallback_email=email)
    identity = session.exec(
        select(UserIdentity).where(
            UserIdentity.provider == "scim",
            UserIdentity.provider_subject == subject,
        )
    ).first()
    if identity is None:
        identity = UserIdentity(
            user_id=user.id,
            provider="scim",
            provider_subject=subject,
            email=email,
            display_name=display_name,
            created_at=occurred_at,
        )
    identity.user_id = user.id
    identity.email = email
    identity.display_name = display_name
    session.add(identity)
    return identity


def _ensure_scim_identity_for_user(
    session: Session,
    *,
    org: Organization,
    user: User,
    occurred_at: datetime,
) -> UserIdentity:
    identity = session.exec(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "scim",
            UserIdentity.provider_subject.like(f"{org.id}:%"),
        )
    ).first()
    if identity is None:
        identity = UserIdentity(
            user_id=user.id,
            provider="scim",
            provider_subject=f"{org.id}:{user.email.lower()}",
            email=user.email,
            display_name=user.display_name or user.email,
            created_at=occurred_at,
        )
    else:
        identity.email = user.email
        identity.display_name = user.display_name or user.email
    session.add(identity)
    return identity


def _resolve_group_member_user(
    session: Session,
    *,
    org: Organization,
    member_value: Any,
    occurred_at: datetime,
) -> tuple[User, UserIdentity]:
    value = str(member_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="SCIM group member value is required")
    user: Optional[User] = None
    if value.isdigit():
        user = session.get(User, int(value))
    if user is None:
        user = session.exec(select(User).where(User.email == value.lower())).first()
    if user is None:
        raise HTTPException(status_code=404, detail=f"SCIM member not found: {value}")
    identity = _ensure_scim_identity_for_user(session, org=org, user=user, occurred_at=occurred_at)
    return user, identity


def _member_user_ids_from_payload(
    session: Session,
    *,
    org: Organization,
    members_payload: Any,
    occurred_at: datetime,
) -> set[int]:
    if members_payload is None:
        return set()
    if not isinstance(members_payload, list):
        raise HTTPException(status_code=400, detail="SCIM group members must be a list")
    user_ids: set[int] = set()
    for member in members_payload:
        if isinstance(member, dict):
            value = member.get("value") or member.get("email") or member.get("userName")
        else:
            value = member
        user, _identity = _resolve_group_member_user(
            session,
            org=org,
            member_value=value,
            occurred_at=occurred_at,
        )
        user_ids.add(user.id)
    return user_ids


def _ensure_scim_membership(
    session: Session,
    *,
    org: Organization,
    user: User,
    active: bool,
    occurred_at: datetime,
) -> bool:
    membership = _membership_for_org(session, org_id=org.id, user_id=user.id)
    changed = False
    if active:
        if membership is None:
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=OrgRole.MEMBER.value,
                created_at=occurred_at,
            )
            session.add(membership)
            changed = True
    else:
        if membership is not None:
            session.delete(membership)
            changed = True
        team_ids = [
            team.id
            for team in session.exec(select(Team).where(Team.organization_id == org.id)).all()
        ]
        if team_ids:
            memberships = session.exec(
                select(TeamMembership).where(
                    TeamMembership.user_id == user.id,
                    TeamMembership.team_id.in_(team_ids),
                )
            ).all()
            for team_membership in memberships:
                session.delete(team_membership)
                changed = True
    return changed


def _resolve_scim_user(session: Session, *, org: Organization, user_id: int) -> tuple[User, Optional[UserIdentity], bool]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="SCIM user not found")
    membership = _membership_for_org(session, org_id=org.id, user_id=user.id)
    identity = session.exec(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "scim",
            UserIdentity.provider_subject.like(f"{org.id}:%"),
        )
    ).first()
    if membership is None and identity is None:
        raise HTTPException(status_code=404, detail="SCIM user not found")
    return user, identity, membership is not None


def _ensure_group_team(
    session: Session,
    *,
    org: Organization,
    display_name: str,
    team_id: Optional[int],
) -> Team:
    if team_id is not None:
        team = session.get(Team, team_id)
        if not team or team.organization_id != org.id:
            raise HTTPException(status_code=404, detail="SCIM team target not found")
        return team
    teams = session.exec(select(Team).where(Team.organization_id == org.id)).all()
    for team in teams:
        if (team.name or "").strip().lower() == display_name.strip().lower():
            return team
    team = Team(
        organization_id=org.id,
        name=display_name,
        slug=_slugify(display_name) or None,
    )
    session.add(team)
    session.flush()
    return team


def _apply_group_membership_sync(
    session: Session,
    *,
    org: Organization,
    mapping: TeamGroupMapping,
    desired_user_ids: set[int],
    occurred_at: datetime,
) -> dict[str, int]:
    current_rows = _group_member_rows(session, org=org, external_group=mapping.external_group)
    current_user_ids = {user.id for user, _identity in current_rows}
    affected_user_ids = current_user_ids | desired_user_ids
    totals = {
        "users_added": 0,
        "users_removed": 0,
        "org_memberships_added": 0,
        "org_memberships_removed": 0,
        "team_memberships_added": 0,
        "team_memberships_removed": 0,
    }
    for user_id in affected_user_ids:
        user = session.get(User, user_id)
        if user is None:
            continue
        identity = _ensure_scim_identity_for_user(session, org=org, user=user, occurred_at=occurred_at)
        existing_groups = _identity_groups(identity)
        if user_id in desired_user_ids:
            updated_groups = _groups_with(existing_groups, mapping.external_group)
        else:
            updated_groups = _groups_without(existing_groups, mapping.external_group)
        if updated_groups == existing_groups:
            continue
        if user_id in desired_user_ids:
            totals["users_added"] += 1
        else:
            totals["users_removed"] += 1
        summary = sync_identity_groups(
            session,
            user=user,
            identity=identity,
            provider="scim",
            groups=updated_groups,
            occurred_at=occurred_at,
        )
        totals["org_memberships_added"] += summary["org_memberships_added"]
        totals["org_memberships_removed"] += summary["org_memberships_removed"]
        totals["team_memberships_added"] += summary["team_memberships_added"]
        totals["team_memberships_removed"] += summary["team_memberships_removed"]
    return totals


@router.get("/ServiceProviderConfig")
def service_provider_config():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": False, "maxResults": 0},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer Token"}],
    }


@router.get("/Groups")
def list_groups(
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    mappings = session.exec(
        select(TeamGroupMapping).where(
            TeamGroupMapping.organization_id == org.id,
            TeamGroupMapping.provider == "scim",
        )
    ).all()
    resources = [_serialize_group_resource(session, org=org, mapping=mapping) for mapping in mappings]
    resources.sort(key=lambda row: row["displayName"].lower())
    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": len(resources),
        "itemsPerPage": len(resources),
        "startIndex": 1,
        "Resources": resources,
    }


@router.get("/Groups/{group_id}")
def get_group(
    group_id: int = Path(..., ge=1),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    mapping = _resolve_scim_group_mapping(session, org=org, group_id=group_id)
    return _serialize_group_resource(session, org=org, mapping=mapping)


@router.post("/Groups", status_code=status.HTTP_201_CREATED)
def create_group(
    payload: dict[str, Any] = Body(...),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    display_name = str(payload.get("displayName") or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="SCIM group payload is missing displayName")
    extension = payload.get(LASTPING_GROUP_SCHEMA) or {}
    if extension is not None and not isinstance(extension, dict):
        raise HTTPException(status_code=400, detail="SCIM group extension payload must be an object")
    team_id = extension.get("teamId")
    role = str(extension.get("role") or TeamRole.MEMBER.value).strip().lower()
    if role not in {TeamRole.MEMBER.value, TeamRole.LEAD.value}:
        raise HTTPException(status_code=400, detail="SCIM group role must be member or lead")
    now = datetime.utcnow()

    existing = session.exec(
        select(TeamGroupMapping).where(
            TeamGroupMapping.organization_id == org.id,
            TeamGroupMapping.provider == "scim",
            TeamGroupMapping.external_group == display_name,
        )
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="SCIM group already exists")

    team = _ensure_group_team(session, org=org, display_name=display_name, team_id=team_id)
    mapping = TeamGroupMapping(
        organization_id=org.id,
        team_id=team.id,
        provider="scim",
        external_group=display_name,
        role=role,
        created_at=now,
    )
    session.add(mapping)
    session.flush()
    desired_user_ids = _member_user_ids_from_payload(
        session,
        org=org,
        members_payload=payload.get("members"),
        occurred_at=now,
    )
    sync_totals = _apply_group_membership_sync(
        session,
        org=org,
        mapping=mapping,
        desired_user_ids=desired_user_ids,
        occurred_at=now,
    )
    session.commit()
    session.refresh(mapping)
    _record_scim_audit(
        session,
        org=org,
        action="scim_create_group",
        target_type="group",
        target_id=mapping.id,
        details=(
            f"display_name={mapping.external_group}, team_id={mapping.team_id}, role={mapping.role}, "
            f"users_added={sync_totals['users_added']}, users_removed={sync_totals['users_removed']}"
        ),
    )
    return _serialize_group_resource(session, org=org, mapping=mapping)


@router.patch("/Groups/{group_id}")
def patch_group(
    group_id: int = Path(..., ge=1),
    payload: dict[str, Any] = Body(...),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    schemas = payload.get("schemas") or []
    if schemas and SCIM_PATCH_SCHEMA not in schemas:
        raise HTTPException(status_code=400, detail="Unsupported SCIM patch schema")
    mapping = _resolve_scim_group_mapping(session, org=org, group_id=group_id)
    now = datetime.utcnow()
    old_display_name = mapping.external_group
    new_display_name = mapping.external_group
    new_team_id = mapping.team_id
    new_role = mapping.role
    desired_user_ids: Optional[set[int]] = None

    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise HTTPException(status_code=400, detail="SCIM patch payload is missing Operations")

    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_name = str(operation.get("op") or "").strip().lower()
        path = str(operation.get("path") or "").strip().lower()
        value = operation.get("value")
        if path.endswith("displayname"):
            new_display_name = str(value or "").strip() or new_display_name
        elif path.endswith("members"):
            if op_name == "remove":
                desired_user_ids = set()
            else:
                desired_user_ids = _member_user_ids_from_payload(
                    session,
                    org=org,
                    members_payload=value,
                    occurred_at=now,
                )
        elif path.endswith("teamid"):
            if value is None:
                raise HTTPException(status_code=400, detail="SCIM group teamId cannot be empty")
            new_team_id = int(value)
        elif path.endswith("role"):
            candidate_role = str(value or "").strip().lower()
            if candidate_role not in {TeamRole.MEMBER.value, TeamRole.LEAD.value}:
                raise HTTPException(status_code=400, detail="SCIM group role must be member or lead")
            new_role = candidate_role
        elif not path and isinstance(value, dict):
            if value.get("displayName"):
                new_display_name = str(value.get("displayName") or "").strip() or new_display_name
            if "members" in value:
                desired_user_ids = _member_user_ids_from_payload(
                    session,
                    org=org,
                    members_payload=value.get("members"),
                    occurred_at=now,
                )
            extension = value.get(LASTPING_GROUP_SCHEMA) or {}
            if isinstance(extension, dict):
                if extension.get("teamId") is not None:
                    new_team_id = int(extension["teamId"])
                if extension.get("role"):
                    candidate_role = str(extension["role"]).strip().lower()
                    if candidate_role not in {TeamRole.MEMBER.value, TeamRole.LEAD.value}:
                        raise HTTPException(status_code=400, detail="SCIM group role must be member or lead")
                    new_role = candidate_role

    if new_display_name != old_display_name:
        conflict = session.exec(
            select(TeamGroupMapping).where(
                TeamGroupMapping.organization_id == org.id,
                TeamGroupMapping.provider == "scim",
                TeamGroupMapping.external_group == new_display_name,
                TeamGroupMapping.id != mapping.id,
            )
        ).first()
        if conflict is not None:
            raise HTTPException(status_code=409, detail="SCIM group displayName already exists")
        affected_rows = _group_member_rows(session, org=org, external_group=old_display_name)
        mapping.external_group = new_display_name
        session.add(mapping)
        for user, identity in affected_rows:
            updated_groups = _groups_with(_groups_without(_identity_groups(identity), old_display_name), new_display_name)
            sync_identity_groups(
                session,
                user=user,
                identity=identity,
                provider="scim",
                groups=updated_groups,
                occurred_at=now,
            )

    if new_team_id != mapping.team_id:
        team = session.get(Team, new_team_id)
        if not team or team.organization_id != org.id:
            raise HTTPException(status_code=404, detail="SCIM team target not found")
        mapping.team_id = new_team_id
        session.add(mapping)

    if new_role != mapping.role:
        mapping.role = new_role
        session.add(mapping)
        if desired_user_ids is None:
            desired_user_ids = {user.id for user, _identity in _group_member_rows(session, org=org, external_group=mapping.external_group)}

    sync_totals = {
        "users_added": 0,
        "users_removed": 0,
        "org_memberships_added": 0,
        "org_memberships_removed": 0,
        "team_memberships_added": 0,
        "team_memberships_removed": 0,
    }
    if desired_user_ids is not None:
        sync_totals = _apply_group_membership_sync(
            session,
            org=org,
            mapping=mapping,
            desired_user_ids=desired_user_ids,
            occurred_at=now,
        )

    session.commit()
    session.refresh(mapping)
    _record_scim_audit(
        session,
        org=org,
        action="scim_patch_group",
        target_type="group",
        target_id=mapping.id,
        details=(
            f"display_name={mapping.external_group}, team_id={mapping.team_id}, role={mapping.role}, "
            f"users_added={sync_totals['users_added']}, users_removed={sync_totals['users_removed']}"
        ),
    )
    return _serialize_group_resource(session, org=org, mapping=mapping)


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: int = Path(..., ge=1),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    mapping = _resolve_scim_group_mapping(session, org=org, group_id=group_id)
    now = datetime.utcnow()
    display_name = mapping.external_group
    affected_rows = _group_member_rows(session, org=org, external_group=mapping.external_group)
    session.delete(mapping)
    for user, identity in affected_rows:
        updated_groups = _groups_without(_identity_groups(identity), mapping.external_group)
        sync_identity_groups(
            session,
            user=user,
            identity=identity,
            provider="scim",
            groups=updated_groups,
            occurred_at=now,
        )
    session.commit()
    _record_scim_audit(
        session,
        org=org,
        action="scim_delete_group",
        target_type="group",
        target_id=group_id,
        details=f"display_name={display_name}",
    )


@router.get("/Users")
def list_users(
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    memberships = session.exec(
        select(OrganizationMembership).where(OrganizationMembership.organization_id == org.id)
    ).all()
    resources: list[dict[str, Any]] = []
    for membership in memberships:
        user = session.get(User, membership.user_id)
        if user is None:
            continue
        identity = session.exec(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == "scim",
                UserIdentity.provider_subject.like(f"{org.id}:%"),
            )
        ).first()
        resources.append(_serialize_user_resource(org=org, user=user, identity=identity, active=True))
    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": len(resources),
        "itemsPerPage": len(resources),
        "startIndex": 1,
        "Resources": resources,
    }


@router.get("/Users/{user_id}")
def get_user(
    user_id: int = Path(..., ge=1),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    user, identity, active = _resolve_scim_user(session, org=org, user_id=user_id)
    return _serialize_user_resource(org=org, user=user, identity=identity, active=active)


@router.post("/Users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: dict[str, Any] = Body(...),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    now = datetime.utcnow()
    email = _extract_email(payload)
    display_name = _extract_display_name(payload, fallback_email=email)
    groups = _extract_groups(payload)
    active = bool(payload.get("active", True))

    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(
            email=email,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            display_name=display_name,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        if display_name and user.display_name != display_name:
            user.display_name = display_name
            session.add(user)

    identity = _upsert_scim_identity(
        session,
        org=org,
        user=user,
        payload=payload,
        display_name=display_name,
        email=email,
        occurred_at=now,
    )
    sync_summary = None
    if active:
        _ensure_scim_membership(session, org=org, user=user, active=True, occurred_at=now)
        sync_summary = sync_identity_groups(
            session,
            user=user,
            identity=identity,
            provider="scim",
            groups=groups,
            occurred_at=now,
        )
    else:
        _deprovision_scim_access(
            session,
            org=org,
            user=user,
            identity=identity,
            occurred_at=now,
            remove_identity=False,
        )
    session.commit()
    session.refresh(user)
    session.refresh(identity)
    _record_scim_audit(
        session,
        org=org,
        action="scim_provision_user",
        target_id=user.id,
        details=(
            f"email={email}, active={active}, groups={len(groups)}, "
            f"org_added={(sync_summary or {}).get('org_memberships_added', 0)}, "
            f"org_removed={(sync_summary or {}).get('org_memberships_removed', 0)}, "
            f"team_added={(sync_summary or {}).get('team_memberships_added', 0)}, "
            f"team_removed={(sync_summary or {}).get('team_memberships_removed', 0)}"
        ),
    )
    return _serialize_user_resource(org=org, user=user, identity=identity, active=active)


@router.patch("/Users/{user_id}")
def patch_user(
    user_id: int = Path(..., ge=1),
    payload: dict[str, Any] = Body(...),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    schemas = payload.get("schemas") or []
    if schemas and SCIM_PATCH_SCHEMA not in schemas:
        raise HTTPException(status_code=400, detail="Unsupported SCIM patch schema")
    user, identity, active = _resolve_scim_user(session, org=org, user_id=user_id)
    now = datetime.utcnow()
    new_active = active
    new_email = user.email
    new_display_name = user.display_name or user.email
    new_groups = _identity_groups(identity)

    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise HTTPException(status_code=400, detail="SCIM patch payload is missing Operations")

    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_name = str(operation.get("op") or "").strip().lower()
        path = str(operation.get("path") or "").strip().lower()
        value = operation.get("value")
        if path == "active":
            new_active = bool(value)
        elif path in {"username", "emails"}:
            if path == "username":
                new_email = str(value or "").strip().lower() or new_email
            elif isinstance(value, list) and value:
                probe = value[0]
                if isinstance(probe, dict) and probe.get("value"):
                    new_email = str(probe["value"]).strip().lower()
                elif isinstance(probe, str) and probe.strip():
                    new_email = probe.strip().lower()
        elif path in {"displayname", "name.formatted"}:
            new_display_name = str(value or "").strip() or new_display_name
        elif path == "groups":
            if op_name == "remove":
                new_groups = []
            elif isinstance(value, list):
                normalized = []
                for item in value:
                    if isinstance(item, dict):
                        probe = str(item.get("display") or item.get("value") or "").strip()
                    else:
                        probe = str(item or "").strip()
                    if probe:
                        normalized.append(probe)
                new_groups = normalized

    if new_email != user.email:
        user.email = new_email
    if new_display_name != (user.display_name or user.email):
        user.display_name = new_display_name
    session.add(user)

    if identity is None:
        identity = _upsert_scim_identity(
            session,
            org=org,
            user=user,
            payload={"externalId": str(user.id), "userName": new_email},
            display_name=new_display_name,
            email=new_email,
            occurred_at=now,
        )
    else:
        identity.email = new_email
        identity.display_name = new_display_name
        session.add(identity)

    sync_summary = None
    if new_active:
        _ensure_scim_membership(session, org=org, user=user, active=True, occurred_at=now)
        sync_summary = sync_identity_groups(
            session,
            user=user,
            identity=identity,
            provider="scim",
            groups=new_groups,
            occurred_at=now,
        )
    else:
        _deprovision_scim_access(
            session,
            org=org,
            user=user,
            identity=identity,
            occurred_at=now,
            remove_identity=False,
        )
    session.commit()
    session.refresh(user)
    if identity is not None:
        session.refresh(identity)
    _record_scim_audit(
        session,
        org=org,
        action="scim_patch_user",
        target_id=user.id,
        details=(
            f"active={new_active}, groups={len(new_groups)}, "
            f"org_added={(sync_summary or {}).get('org_memberships_added', 0)}, "
            f"org_removed={(sync_summary or {}).get('org_memberships_removed', 0)}, "
            f"team_added={(sync_summary or {}).get('team_memberships_added', 0)}, "
            f"team_removed={(sync_summary or {}).get('team_memberships_removed', 0)}"
        ),
    )
    return _serialize_user_resource(org=org, user=user, identity=identity, active=new_active)


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int = Path(..., ge=1),
    org: Organization = Depends(_get_scim_org),
    session: Session = Depends(get_session),
):
    user, identity, _active = _resolve_scim_user(session, org=org, user_id=user_id)
    now = datetime.utcnow()
    _deprovision_scim_access(
        session,
        org=org,
        user=user,
        identity=identity,
        occurred_at=now,
        remove_identity=True,
    )
    session.commit()
    _record_scim_audit(
        session,
        org=org,
        action="scim_delete_user",
        target_id=user.id,
        details=f"user_id={user.id}",
    )

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, status
from sqlmodel import Session, select

from ..db import get_session
from ..identity_sync import sync_identity_groups
from ..models import AuditLog, OrgRole, Organization, OrganizationMembership, Team, TeamMembership, User, UserIdentity
from ..security import hash_password

router = APIRouter(prefix="/scim/v2", tags=["scim"])

SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


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
    target_id: Optional[int],
    details: Optional[str],
) -> None:
    session.add(
        AuditLog(
            actor=f"scim:org:{org.id}",
            action=action,
            target_type="user",
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

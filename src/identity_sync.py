from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from .models import (
    OrgRole,
    OrganizationGroupMapping,
    OrganizationMembership,
    Team,
    TeamGroupMapping,
    TeamMembership,
    TeamRole,
    User,
    UserIdentity,
)

_ORG_ROLE_RANK = {
    OrgRole.MEMBER.value: 1,
    OrgRole.ADMIN.value: 2,
    OrgRole.OWNER.value: 3,
}

_TEAM_ROLE_RANK = {
    TeamRole.MEMBER.value: 1,
    TeamRole.LEAD.value: 2,
}


def _normalize_groups(groups: Any) -> list[str]:
    if groups is None:
        return []
    if isinstance(groups, str):
        values = [groups]
    elif isinstance(groups, (list, tuple, set)):
        values = [str(item or "").strip() for item in groups]
    else:
        values = [str(groups).strip()]
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    unique.sort(key=str.lower)
    return unique


def _org_role_at_least(current_role: Optional[str], required_role: str) -> bool:
    return _ORG_ROLE_RANK.get(current_role or "", 0) >= _ORG_ROLE_RANK.get(required_role, 0)


def _team_role_at_least(current_role: Optional[str], required_role: str) -> bool:
    return _TEAM_ROLE_RANK.get(current_role or "", 0) >= _TEAM_ROLE_RANK.get(required_role, 0)


def _set_identity_groups(identity: UserIdentity, groups: list[str]) -> None:
    identity.last_groups_json = json.dumps(groups, separators=(",", ":"), sort_keys=True)


def sync_identity_groups(
    session: Session,
    *,
    user: User,
    identity: UserIdentity,
    provider: str,
    groups: Any,
    occurred_at: Optional[datetime] = None,
) -> dict[str, int]:
    normalized_groups = _normalize_groups(groups)
    group_lookup = {item.lower() for item in normalized_groups}
    now = occurred_at or datetime.utcnow()
    summary = {
        "group_count": len(normalized_groups),
        "org_memberships_added": 0,
        "org_roles_upgraded": 0,
        "team_memberships_added": 0,
        "team_roles_upgraded": 0,
    }

    _set_identity_groups(identity, normalized_groups)
    session.add(identity)
    if not group_lookup:
        return summary

    org_mappings = session.exec(
        select(OrganizationGroupMapping).where(OrganizationGroupMapping.provider == provider)
    ).all()
    for mapping in org_mappings:
        if mapping.external_group.strip().lower() not in group_lookup:
            continue
        membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == mapping.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        if membership is None:
            membership = OrganizationMembership(
                organization_id=mapping.organization_id,
                user_id=user.id,
                role=mapping.role,
                created_at=now,
            )
            summary["org_memberships_added"] += 1
        elif not _org_role_at_least(membership.role, mapping.role):
            membership.role = mapping.role
            summary["org_roles_upgraded"] += 1
        session.add(membership)

    team_mappings = session.exec(
        select(TeamGroupMapping).where(TeamGroupMapping.provider == provider)
    ).all()
    for mapping in team_mappings:
        if mapping.external_group.strip().lower() not in group_lookup:
            continue
        team = session.get(Team, mapping.team_id)
        if team is None or team.organization_id != mapping.organization_id:
            continue
        org_membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == mapping.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        if org_membership is None:
            org_membership = OrganizationMembership(
                organization_id=mapping.organization_id,
                user_id=user.id,
                role=OrgRole.MEMBER.value,
                created_at=now,
            )
            session.add(org_membership)
            summary["org_memberships_added"] += 1

        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == mapping.team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        if membership is None:
            membership = TeamMembership(
                team_id=mapping.team_id,
                user_id=user.id,
                role=mapping.role,
                created_at=now,
            )
            summary["team_memberships_added"] += 1
        elif not _team_role_at_least(membership.role, mapping.role):
            membership.role = mapping.role
            summary["team_roles_upgraded"] += 1
        session.add(membership)
    return summary

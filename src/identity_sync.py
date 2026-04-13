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


def _max_rank_role(*roles: Optional[str], rank_map: dict[str, int]) -> Optional[str]:
    candidates = [role for role in roles if role]
    if not candidates:
        return None
    return max(candidates, key=lambda role: rank_map.get(role or "", 0))


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
        "org_memberships_removed": 0,
        "org_roles_downgraded": 0,
        "team_memberships_added": 0,
        "team_roles_upgraded": 0,
        "team_memberships_removed": 0,
        "team_roles_downgraded": 0,
    }

    _set_identity_groups(identity, normalized_groups)
    session.add(identity)

    desired_org_roles: dict[int, tuple[str, str]] = {}
    desired_team_roles: dict[int, tuple[int, str, str]] = {}
    org_mappings = session.exec(
        select(OrganizationGroupMapping).where(OrganizationGroupMapping.provider == provider)
    ).all()
    for mapping in org_mappings:
        if mapping.external_group.strip().lower() not in group_lookup:
            continue
        current = desired_org_roles.get(mapping.organization_id)
        if current is None or _ORG_ROLE_RANK[mapping.role] > _ORG_ROLE_RANK[current[0]]:
            desired_org_roles[mapping.organization_id] = (mapping.role, mapping.external_group)

    team_mappings = session.exec(
        select(TeamGroupMapping).where(TeamGroupMapping.provider == provider)
    ).all()
    for mapping in team_mappings:
        if mapping.external_group.strip().lower() not in group_lookup:
            continue
        current = desired_team_roles.get(mapping.team_id)
        if current is None or _TEAM_ROLE_RANK[mapping.role] > _TEAM_ROLE_RANK[current[1]]:
            desired_team_roles[mapping.team_id] = (mapping.organization_id, mapping.role, mapping.external_group)
        org_current = desired_org_roles.get(mapping.organization_id)
        implied_org_role = OrgRole.MEMBER.value
        if org_current is None or _ORG_ROLE_RANK[implied_org_role] > _ORG_ROLE_RANK[org_current[0]]:
            desired_org_roles[mapping.organization_id] = (implied_org_role, mapping.external_group)

    org_memberships = session.exec(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
    ).all()
    org_membership_by_org = {membership.organization_id: membership for membership in org_memberships}
    managed_org_memberships = {
        membership.organization_id: membership
        for membership in org_memberships
        if (membership.managed_provider or "") == provider
    }

    for organization_id, (desired_role, source_group) in desired_org_roles.items():
        membership = org_membership_by_org.get(organization_id)
        if membership is None:
            membership = OrganizationMembership(
                organization_id=organization_id,
                user_id=user.id,
                role=desired_role,
                managed_provider=provider,
                managed_group=source_group,
                managed_fallback_role=None,
                managed_last_synced_at=now,
                created_at=now,
            )
            summary["org_memberships_added"] += 1
            org_membership_by_org[organization_id] = membership
            session.add(membership)
            continue

        if membership.managed_provider == provider:
            target_role = _max_rank_role(membership.managed_fallback_role, desired_role, rank_map=_ORG_ROLE_RANK)
            if target_role and _ORG_ROLE_RANK.get(target_role, 0) > _ORG_ROLE_RANK.get(membership.role or "", 0):
                summary["org_roles_upgraded"] += 1
            elif target_role and _ORG_ROLE_RANK.get(target_role, 0) < _ORG_ROLE_RANK.get(membership.role or "", 0):
                summary["org_roles_downgraded"] += 1
            membership.role = target_role or desired_role
            membership.managed_group = source_group
            membership.managed_last_synced_at = now
            session.add(membership)
            continue

        if not _org_role_at_least(membership.role, desired_role):
            membership.managed_provider = provider
            membership.managed_group = source_group
            membership.managed_fallback_role = membership.role
            membership.managed_last_synced_at = now
            membership.role = desired_role
            summary["org_roles_upgraded"] += 1
            session.add(membership)

    for organization_id, membership in managed_org_memberships.items():
        if organization_id in desired_org_roles:
            continue
        if membership.managed_fallback_role:
            if _ORG_ROLE_RANK.get(membership.managed_fallback_role, 0) < _ORG_ROLE_RANK.get(membership.role or "", 0):
                summary["org_roles_downgraded"] += 1
            membership.role = membership.managed_fallback_role
            membership.managed_provider = None
            membership.managed_group = None
            membership.managed_fallback_role = None
            membership.managed_last_synced_at = None
            session.add(membership)
        else:
            session.delete(membership)
            summary["org_memberships_removed"] += 1

    team_memberships = session.exec(
        select(TeamMembership).where(TeamMembership.user_id == user.id)
    ).all()
    team_membership_by_team = {membership.team_id: membership for membership in team_memberships}
    managed_team_memberships = {
        membership.team_id: membership
        for membership in team_memberships
        if (membership.managed_provider or "") == provider
    }

    for team_id, (organization_id, desired_role, source_group) in desired_team_roles.items():
        team = session.get(Team, team_id)
        if team is None or team.organization_id != organization_id:
            continue
        org_membership = org_membership_by_org.get(organization_id)
        if org_membership is None:
            org_membership = OrganizationMembership(
                organization_id=organization_id,
                user_id=user.id,
                role=OrgRole.MEMBER.value,
                managed_provider=provider,
                managed_group=source_group,
                managed_fallback_role=None,
                managed_last_synced_at=now,
                created_at=now,
            )
            session.add(org_membership)
            org_membership_by_org[organization_id] = org_membership
            summary["org_memberships_added"] += 1

        membership = team_membership_by_team.get(team_id)
        if membership is None:
            membership = TeamMembership(
                team_id=team_id,
                user_id=user.id,
                role=desired_role,
                managed_provider=provider,
                managed_group=source_group,
                managed_fallback_role=None,
                managed_last_synced_at=now,
                created_at=now,
            )
            summary["team_memberships_added"] += 1
            team_membership_by_team[team_id] = membership
            session.add(membership)
            continue
        if membership.managed_provider == provider:
            target_role = _max_rank_role(membership.managed_fallback_role, desired_role, rank_map=_TEAM_ROLE_RANK)
            if target_role and _TEAM_ROLE_RANK.get(target_role, 0) > _TEAM_ROLE_RANK.get(membership.role or "", 0):
                summary["team_roles_upgraded"] += 1
            elif target_role and _TEAM_ROLE_RANK.get(target_role, 0) < _TEAM_ROLE_RANK.get(membership.role or "", 0):
                summary["team_roles_downgraded"] += 1
            membership.role = target_role or desired_role
            membership.managed_group = source_group
            membership.managed_last_synced_at = now
            session.add(membership)
            continue
        if not _team_role_at_least(membership.role, desired_role):
            membership.managed_provider = provider
            membership.managed_group = source_group
            membership.managed_fallback_role = membership.role
            membership.managed_last_synced_at = now
            membership.role = desired_role
            summary["team_roles_upgraded"] += 1
        session.add(membership)

    for team_id, membership in managed_team_memberships.items():
        if team_id in desired_team_roles:
            continue
        if membership.managed_fallback_role:
            if _TEAM_ROLE_RANK.get(membership.managed_fallback_role, 0) < _TEAM_ROLE_RANK.get(membership.role or "", 0):
                summary["team_roles_downgraded"] += 1
            membership.role = membership.managed_fallback_role
            membership.managed_provider = None
            membership.managed_group = None
            membership.managed_fallback_role = None
            membership.managed_last_synced_at = None
            session.add(membership)
        else:
            session.delete(membership)
            summary["team_memberships_removed"] += 1
    return summary

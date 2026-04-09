"""
Dependency helpers for request authentication and authorization.

Project API keys are accepted only via `X-API-KEY`.
User session tokens are accepted only via `Authorization: Bearer <token>`.
"""

import importlib
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session, select

from .db import get_session
from .models import (
    ApiKey,
    ApiKeyUsage,
    OrgRole,
    OrganizationMembership,
    Project,
    ProjectMembership,
    ProjectTeamAccess,
    Role,
    TeamMembership,
    User,
    UserToken,
    UserUsage,
)
from .security import fingerprint_token, hash_api_key, verify_api_key

# Optional Redis support for distributed rate limiting
_redis = None
if os.environ.get('REDIS_URL'):
    try:
        redis_spec = importlib.util.find_spec('redis')
        if redis_spec is not None:
            import redis
            _redis = redis.from_url(os.environ.get('REDIS_URL'))
    except Exception:
        _redis = None

# In-memory fallback counters for user-token rate limiting when Redis is not configured.
_user_counters: dict = {}
_public_counters: dict = {}
_CACHE_MISS = object()
_PROJECT_ROLE_RANK = {
    Role.VIEWER.value: 1,
    Role.EDITOR.value: 2,
    Role.ADMIN.value: 3,
    Role.OWNER.value: 4,
}
_ORG_ROLE_RANK = {
    OrgRole.MEMBER.value: 1,
    OrgRole.ADMIN.value: 2,
    OrgRole.OWNER.value: 3,
}


def _deps_cache(session: Session) -> dict:
    return session.info.setdefault("deps_cache", {})


def _get_cached_project(session: Session, project_id: int) -> Optional[Project]:
    cache = _deps_cache(session)
    cache_key = ("project", project_id)
    project = cache.get(cache_key, _CACHE_MISS)
    if project is _CACHE_MISS:
        project = session.get(Project, project_id)
        cache[cache_key] = project
    return project


def _get_cached_user_token(session: Session, token: str) -> Optional[UserToken]:
    cache = _deps_cache(session)
    cache_key = ("user_token", token)
    user_token = cache.get(cache_key, _CACHE_MISS)
    if user_token is _CACHE_MISS:
        token_fingerprint = fingerprint_token(token)
        user_token = session.exec(
            select(UserToken).where(UserToken.token_fingerprint == token_fingerprint)
        ).first()
        if user_token and _verify_user_token(token, user_token.token):
            cache[cache_key] = user_token
            return user_token

        # Backward compatibility for legacy plaintext session rows. Upgrade
        # the row in place once it is successfully matched.
        legacy = session.exec(select(UserToken).where(UserToken.token == token)).first()
        if legacy:
            legacy.token = hash_api_key(token)
            legacy.token_fingerprint = token_fingerprint
            session.add(legacy)
            session.commit()
            session.refresh(legacy)
            user_token = legacy
        else:
            user_token = None
        cache[cache_key] = user_token
    return user_token


def _verify_user_token(raw_token: str, stored_token: str) -> bool:
    if not raw_token or not stored_token:
        return False
    if stored_token.startswith("pbkdf2_sha256$"):
        return verify_api_key(raw_token, stored_token)
    return secrets.compare_digest(raw_token, stored_token)


def _get_cached_user(session: Session, user_id: int) -> Optional[User]:
    cache = _deps_cache(session)
    cache_key = ("user", user_id)
    user = cache.get(cache_key, _CACHE_MISS)
    if user is _CACHE_MISS:
        user = session.get(User, user_id)
        cache[cache_key] = user
    return user


def _get_cached_membership(session: Session, user_id: int, project_id: int) -> Optional[ProjectMembership]:
    cache = _deps_cache(session)
    cache_key = ("membership", user_id, project_id)
    membership = cache.get(cache_key, _CACHE_MISS)
    if membership is _CACHE_MISS:
        membership = session.exec(
            select(ProjectMembership).where(
                ProjectMembership.user_id == user_id,
                ProjectMembership.project_id == project_id,
            )
        ).first()
        cache[cache_key] = membership
    return membership


def _get_cached_org_membership(session: Session, user_id: int, org_id: int) -> Optional[OrganizationMembership]:
    cache = _deps_cache(session)
    cache_key = ("org_membership", user_id, org_id)
    membership = cache.get(cache_key, _CACHE_MISS)
    if membership is _CACHE_MISS:
        membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == org_id,
            )
        ).first()
        cache[cache_key] = membership
    return membership


def _get_cached_team_membership(session: Session, user_id: int, team_id: int) -> Optional[TeamMembership]:
    cache = _deps_cache(session)
    cache_key = ("team_membership", user_id, team_id)
    membership = cache.get(cache_key, _CACHE_MISS)
    if membership is _CACHE_MISS:
        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.user_id == user_id,
                TeamMembership.team_id == team_id,
            )
        ).first()
        cache[cache_key] = membership
    return membership


def _get_cached_project_team_access(session: Session, project_id: int) -> list[ProjectTeamAccess]:
    cache = _deps_cache(session)
    cache_key = ("project_team_access", project_id)
    access_rows = cache.get(cache_key, _CACHE_MISS)
    if access_rows is _CACHE_MISS:
        access_rows = list(
            session.exec(select(ProjectTeamAccess).where(ProjectTeamAccess.project_id == project_id)).all()
        )
        cache[cache_key] = access_rows
    return access_rows


def _project_role_rank(role: Optional[str]) -> int:
    if not role:
        return 0
    return _PROJECT_ROLE_RANK.get(role, 0)


def _project_role_at_least(current_role: Optional[str], required_role: str) -> bool:
    return _project_role_rank(current_role) >= _project_role_rank(required_role)


def _org_role_at_least(current_role: Optional[str], required_role: str) -> bool:
    return _ORG_ROLE_RANK.get(current_role or "", 0) >= _ORG_ROLE_RANK.get(required_role, 0)


def _role_required_detail(role: str) -> str:
    if role == Role.OWNER.value:
        return "Owner role required"
    if role == Role.ADMIN.value:
        return "Admin or owner role required"
    if role == Role.EDITOR.value:
        return "Editor role required"
    return "Viewer role required"


def get_effective_project_role(session: Session, user_id: int, project_id: int) -> Optional[str]:
    cache = _deps_cache(session)
    cache_key = ("effective_project_role", user_id, project_id)
    effective_role = cache.get(cache_key, _CACHE_MISS)
    if effective_role is not _CACHE_MISS:
        return effective_role

    candidates: list[str] = []
    direct_membership = _get_cached_membership(session, user_id, project_id)
    if direct_membership:
        if direct_membership.role == Role.OWNER.value:
            cache[cache_key] = direct_membership.role
            return direct_membership.role
        candidates.append(direct_membership.role)

    project = _get_cached_project(session, project_id)
    if project and project.org_id:
        org_membership = _get_cached_org_membership(session, user_id, project.org_id)
        if org_membership:
            if org_membership.role == OrgRole.OWNER.value:
                candidates.append(Role.OWNER.value)
            elif org_membership.role == OrgRole.ADMIN.value:
                candidates.append(Role.ADMIN.value)

    for access in _get_cached_project_team_access(session, project_id):
        if _get_cached_team_membership(session, user_id, access.team_id):
            candidates.append(access.role)

    effective_role = max(candidates, key=_project_role_rank) if candidates else None
    cache[cache_key] = effective_role
    return effective_role


def get_effective_org_role(session: Session, user_id: int, org_id: int) -> Optional[str]:
    membership = _get_cached_org_membership(session, user_id, org_id)
    if not membership:
        return None
    return membership.role


def authorize_org_operation(
    org_id: int,
    *,
    min_role: str = OrgRole.MEMBER.value,
    x_admin_token: Optional[str] = None,
    authorization: Optional[str] = None,
    session: Session,
) -> OrganizationMembership:
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token and x_admin_token and x_admin_token == admin_token:
        membership = OrganizationMembership(organization_id=org_id, user_id=0, role=OrgRole.OWNER.value)
        return membership

    ut = _get_valid_user_token(session, authorization)
    membership = _get_cached_org_membership(session, ut.user_id, org_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an organization member")
    if not _org_role_at_least(membership.role, min_role):
        detail = "Organization admin or owner role required" if min_role == OrgRole.ADMIN.value else "Organization owner role required"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return membership


def authorize_project_operation(
    project_id: int,
    *,
    min_role: str = Role.VIEWER.value,
    x_admin_token: Optional[str] = None,
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
    session: Session,
) -> Project:
    admin_token = os.environ.get("ADMIN_TOKEN")
    project = _get_cached_project(session, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if admin_token and x_admin_token and x_admin_token == admin_token:
        return project

    if x_api_key:
        matched, project_primary = _match_project_api_key(session, project_id, x_api_key)
        if not matched and not project_primary:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
        role = Role.OWNER.value if project_primary else (matched.role or Role.OWNER.value)
        if not _project_role_at_least(role, min_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_role_required_detail(min_role))
        return project

    bearer = _extract_bearer_token(authorization)
    if bearer:
        ut = _get_valid_user_token(session, authorization)
        role = get_effective_project_role(session, ut.user_id, project_id)
        if not role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
        if not _project_role_at_least(role, min_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_role_required_detail(min_role))
        return project

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization.split(None, 1)[1].strip()
    return None


def _get_valid_user_token(session: Session, authorization: Optional[str]) -> UserToken:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    ut = _get_cached_user_token(session, token)
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if ut.expires_at and ut.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    return ut


def _match_project_api_key(session: Session, project_id: int, key: str) -> tuple[Optional[ApiKey], bool]:
    cache = _deps_cache(session)
    cache_key = ("project_api_key_match", project_id, key)
    cached = cache.get(cache_key, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached

    stmt = select(ApiKey).where(ApiKey.project_id == project_id)
    candidates = session.exec(stmt).all()
    matched = None
    for api_key in candidates:
        if not getattr(api_key, "is_active", True):
            continue
        if getattr(api_key, "revoked_at", None):
            continue
        if verify_api_key(key, api_key.key_hash):
            matched = api_key
            break
    if matched:
        result = (matched, False)
    else:
        project = _get_cached_project(session, project_id)
        project_primary = bool(project and getattr(project, "api_key_hash", None) and verify_api_key(key, project.api_key_hash))
        result = (None, project_primary)

    cache[cache_key] = result
    return result


def _get_client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP extraction (supports X-Forwarded-For)."""
    if not request:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Take the first hop (original client)
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else None


def _raise_rate_limit(window_seconds: int) -> None:
    raise HTTPException(
        status_code=429,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(window_seconds)},
    )


def _enforce_counter(counter: dict, key: str, limit: int, window_seconds: int) -> None:
    if limit <= 0:
        return
    cur = counter.get(key, 0) + 1
    counter[key] = cur
    if cur > limit:
        _raise_rate_limit(window_seconds)
    # prevent unbounded memory growth
    if len(counter) > 10000:
        counter.clear()


def _enforce_rate_limit(prefix: str, ident: str, limit: int, window_seconds: int) -> None:
    """Shared rate limit implementation using Redis when available."""
    if limit <= 0 or not ident:
        return
    minute = datetime.utcnow().strftime('%Y%m%d%H%M')
    rkey = f"{prefix}:{ident}:{minute}"
    if _redis is not None:
        try:
            val = _redis.incr(rkey)
            if val == 1:
                _redis.expire(rkey, max(60, int(window_seconds) + 5))
            if val > limit:
                _raise_rate_limit(window_seconds)
            return
        except HTTPException:
            raise
        except Exception:
            # fall back to in-memory
            pass
    _enforce_counter(_public_counters, rkey, limit, window_seconds)


def limit_public_requests(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
) -> None:
    """Rate limit unauthenticated/public endpoints by IP and user token.

    Defaults can be tuned via env:
    - PUBLIC_RATE_LIMIT_PER_MINUTE (IP limit, default 120)
    - PUBLIC_USER_RATE_LIMIT_PER_MINUTE (user token limit, default 120)
    - PUBLIC_RATE_LIMIT_WINDOW_SECONDS (window size, default 60)
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        return

    window = int(os.environ.get("PUBLIC_RATE_LIMIT_WINDOW_SECONDS", "60"))
    ip_limit = int(os.environ.get("PUBLIC_RATE_LIMIT_PER_MINUTE", "120"))
    user_limit = int(os.environ.get("PUBLIC_USER_RATE_LIMIT_PER_MINUTE", "120"))

    client_ip = _get_client_ip(request)
    if client_ip:
        _enforce_rate_limit("pubip", client_ip, ip_limit, window)

    # If a bearer user token is present, also enforce per-user limit.
    token = _extract_bearer_token(authorization)
    if token:
        ut = _get_cached_user_token(session, token)
        if ut and (not ut.expires_at or ut.expires_at >= datetime.utcnow()):
            _enforce_rate_limit("pubuser", str(ut.user_id), user_limit, window)


def require_admin_or_project_api_key(project_id: int, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """Allow access when either a valid project API key is supplied or the admin token matches.

    - If `X-ADMIN-TOKEN` matches `ADMIN_TOKEN` env var, returns the project.
    - Otherwise falls back to verifying the project's API key from `X-API-KEY`
      (same as `require_project_api_key`).
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        project = _get_cached_project(session, project_id)
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return project

    # fall back to project API key verification
    return require_project_api_key(project_id, authorization=authorization, x_api_key=x_api_key, session=session)


def require_project_access(project_id: int, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """Allow read access via admin token, project API key, or project membership."""
    return authorize_project_operation(
        project_id,
        min_role=Role.VIEWER.value,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )


def _extract_api_key(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    """Extract a project API key from `X-API-KEY` only."""
    if isinstance(x_api_key, str) and x_api_key:
        return x_api_key
    return None


def require_project_api_key(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """FastAPI dependency that verifies a project's API key.

    - Reads only `X-API-KEY`.
    - Raises 401 when missing.
    - Raises 403 when provided key does not match stored hash.
    - Returns `Project` on success for downstream use.
    """
    key = _extract_api_key(authorization, x_api_key)
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    project = _get_cached_project(session, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    matched_api_key, project_primary = _match_project_api_key(session, project_id, key)
    if matched_api_key or project_primary:
        return project

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def limit_by_api_key(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Optional[ApiKey]:
    """Enforce per-API-key rate limits for write endpoints.

    - Admin token bypasses rate limiting.
    - Accepts a project-scoped key from `X-API-KEY` / `api_key` table or the
      project's primary API key hash stored on `Project.api_key_hash`.
    - Falls back to bearer user-token rate limits when a user session token is present.
      Raises 401/403 if missing/invalid.
    - Increments a per-minute counter stored in `ApiKeyUsage` and raises 429 when exceeded.
    Returns the matched `ApiKey` on success, or None for admin token.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        return None

    window_seconds = int(os.environ.get("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
    key = _extract_api_key(authorization, x_api_key)
    if key:
        matched, project_primary = _match_project_api_key(session, project_id, key)
        if not matched:
            # Backwards compatibility: allow the project's primary API key stored
            # on `Project.api_key_hash` even when no `ApiKey` rows exist yet.
            if project_primary:
                return None
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

        if not _project_role_at_least(getattr(matched, "role", Role.OWNER.value), Role.EDITOR.value):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key role does not allow write access")

        limit = getattr(matched, 'rate_limit_per_minute', 0) or 0
        if not limit:
            return matched

        # Prefer Redis for distributed counters when configured
        if _redis is not None:
            minute = datetime.utcnow().strftime('%Y%m%d%H%M')
            rkey = f"apik:{matched.id}:{minute}"
            try:
                val = _redis.incr(rkey)
                if val == 1:
                    _redis.expire(rkey, 70)
                if val > limit:
                    _raise_rate_limit(window_seconds)
                return matched
            except HTTPException:
                raise
            except Exception:
                pass

        now = datetime.utcnow().replace(second=0, microsecond=0)
        us = session.exec(select(ApiKeyUsage).where(ApiKeyUsage.api_key_id == matched.id, ApiKeyUsage.minute_start == now)).first()
        if us:
            if us.count >= limit:
                _raise_rate_limit(window_seconds)
            us.count = us.count + 1
            session.add(us)
            session.commit()
        else:
            us = ApiKeyUsage(api_key_id=matched.id, minute_start=now, count=1)
            session.add(us)
            session.commit()
        return matched

    token = _extract_bearer_token(authorization)
    if token:
        ut = _get_valid_user_token(session, authorization)

        limit = int(os.environ.get('USER_RATE_LIMIT_PER_MINUTE', '60'))
        if not limit:
            return ut

        if _redis is not None:
            minute = datetime.utcnow().strftime('%Y%m%d%H%M')
            rkey = f"user:{ut.user_id}:{minute}"
            try:
                val = _redis.incr(rkey)
                if val == 1:
                    _redis.expire(rkey, 70)
                if val > limit:
                    _raise_rate_limit(window_seconds)
                return ut
            except HTTPException:
                raise
            except Exception:
                pass

        now = datetime.utcnow().replace(second=0, microsecond=0)
        try:
            uu = session.exec(select(UserUsage).where(UserUsage.user_id == ut.user_id, UserUsage.minute_start == now)).first()
            if uu:
                if uu.count >= limit:
                    _raise_rate_limit(window_seconds)
                uu.count = uu.count + 1
                session.add(uu)
                session.commit()
            else:
                uu = UserUsage(user_id=ut.user_id, minute_start=now, count=1)
                session.add(uu)
                session.commit()
            return ut
        except Exception:
            minute = datetime.utcnow().strftime('%Y%m%d%H%M')
            rkey = f"user:{ut.user_id}:{minute}"
            cur = _user_counters.get(rkey, 0)
            if cur >= limit:
                _raise_rate_limit(window_seconds)
            _user_counters[rkey] = cur + 1
            if len(_user_counters) > 10000:
                _user_counters.clear()
            return ut

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")


def get_current_user(authorization: Optional[str] = Header(None), session: Session = Depends(get_session)) -> User:
    ut = _get_valid_user_token(session, authorization)
    return _get_cached_user(session, ut.user_id)


def require_project_role(project_id: int, role: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> ProjectMembership:
    effective_role = get_effective_project_role(session, current_user.id, project_id)
    if not effective_role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
    if not _project_role_at_least(effective_role, role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_role_required_detail(role))
    pm = _get_cached_membership(session, current_user.id, project_id)
    if pm:
        return pm
    return ProjectMembership(user_id=current_user.id, project_id=project_id, role=effective_role)


def require_admin_or_owner(project_id: int, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """Allow access when admin token supplied or bearer user is project owner.

    Admin token check is performed first to avoid requiring a bearer token.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        project = _get_cached_project(session, project_id)
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return project

    # validate bearer token and ensure admin-or-owner effective role
    ut = _get_valid_user_token(session, authorization)
    role = get_effective_project_role(session, ut.user_id, project_id)
    if not _project_role_at_least(role, Role.ADMIN.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin or owner role required")

    project = _get_cached_project(session, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_audit_context(request: Optional[Request], authorization: Optional[str], x_admin_token: Optional[str], session: Session) -> tuple[str, Optional[str], Optional[str]]:
    """Return (actor, actor_ip, user_agent) for audit logs.

    - `actor` is 'admin' for admin token, 'user:<id>' for bearer user tokens, or 'unknown'.
    - `actor_ip` and `user_agent` are extracted from the `Request` when available.
    """
    actor = 'unknown'
    actor_ip = None
    user_agent = None
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        actor = 'admin'
    else:
        tok = _extract_bearer_token(authorization)
        ut = _get_cached_user_token(session, tok) if tok else None
        if ut:
            actor = f"user:{ut.user_id}"
    try:
        if request:
            actor_ip = request.client.host if request.client else None
            user_agent = request.headers.get('user-agent')
    except Exception:
        actor_ip = None
        user_agent = None
    return actor, actor_ip, user_agent

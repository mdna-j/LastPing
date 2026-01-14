"""
Dependency helpers for API key verification.

`require_project_api_key` is used by router endpoints to ensure requests
are authenticated using a project's API key. It accepts either an
`Authorization: Bearer <key>` header or `X-API-KEY` header.
"""

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session

from .db import get_session
from .models import Project
from .security import verify_api_key
import os
from fastapi import Header
from datetime import datetime
from sqlmodel import select
from .models import ApiKey, ApiKeyUsage
import importlib
from .models import User, UserToken, ProjectMembership
from datetime import datetime
from fastapi import Header

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


def require_admin_or_project_api_key(project_id: int, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """Allow access when either a valid project API key is supplied or the admin token matches.

    - If `X-ADMIN-TOKEN` matches `ADMIN_TOKEN` env var, returns the project.
    - Otherwise falls back to verifying the project's API key (same as `require_project_api_key`).
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return project

    # fall back to project API key verification
    return require_project_api_key(project_id, authorization=authorization, x_api_key=x_api_key, session=session)


def _extract_api_key(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    """Extract API key from common header locations.

    Supports `Authorization: Bearer <key>` and `X-API-KEY`.
    """
    if authorization:
        if authorization.lower().startswith("bearer "):
            return authorization.split(None, 1)[1].strip()
    if x_api_key:
        return x_api_key
    return None


def require_project_api_key(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """FastAPI dependency that verifies a project's API key.

    - Raises 401 when missing.
    - Raises 403 when provided key does not match stored hash.
    - Returns `Project` on success for downstream use.
    """
    key = _extract_api_key(authorization, x_api_key)
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Only verify against the stored PBKDF2 hash.
    if getattr(project, "api_key_hash", None) and verify_api_key(key, project.api_key_hash):
        return project

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def limit_by_api_key(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Optional[ApiKey]:
    """Enforce per-API-key rate limits for write endpoints.

    - Admin token bypasses rate limiting.
    - Expects an API key belonging to the project; raises 401/403 if missing/invalid.
    - Increments a per-minute counter stored in `ApiKeyUsage` and raises 429 when exceeded.
    Returns the matched `ApiKey` on success, or None for admin token.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if admin_token and x_admin_token and x_admin_token == admin_token:
        return None

    key = _extract_api_key(authorization, x_api_key)
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    # find matching ApiKey for project
    stmt = select(ApiKey).where(ApiKey.project_id == project_id)
    candidates = session.exec(stmt).all()
    matched = None
    for ak in candidates:
        if verify_api_key(key, ak.key_hash):
            matched = ak
            break
    if not matched:
        # If no API key matched, allow bearer user tokens and apply per-user rate limits.
        if authorization and authorization.lower().startswith('bearer '):
            token = authorization.split(None, 1)[1].strip()
            ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
            if not ut:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
            if ut.expires_at and ut.expires_at < datetime.utcnow():
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

            # Rate limit per-user (configurable via env var USER_RATE_LIMIT_PER_MINUTE)
            limit = int(os.environ.get('USER_RATE_LIMIT_PER_MINUTE', '60'))
            if not limit:
                return ut

            # Prefer Redis when available
            if _redis is not None:
                minute = datetime.utcnow().strftime('%Y%m%d%H%M')
                rkey = f"user:{ut.user_id}:{minute}"
                try:
                    val = _redis.incr(rkey)
                    if val == 1:
                        _redis.expire(rkey, 70)
                    if val > limit:
                        raise HTTPException(status_code=429, detail="Rate limit exceeded")
                    return ut
                except HTTPException:
                    raise
                except Exception:
                    pass

            # In-memory fallback (per-process)
            minute = datetime.utcnow().strftime('%Y%m%d%H%M')
            rkey = f"user:{ut.user_id}:{minute}"
            cur = _user_counters.get(rkey, 0)
            if cur >= limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            _user_counters[rkey] = cur + 1
            # prune old keys occasionally
            if len(_user_counters) > 10000:
                _user_counters.clear()
            return ut

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    limit = getattr(matched, 'rate_limit_per_minute', 0) or 0
    if not limit:
        return matched

    # Prefer Redis for distributed counters when configured
    if _redis is not None:
        # key per api_key_id + minute window
        minute = datetime.utcnow().strftime('%Y%m%d%H%M')
        rkey = f"apik:{matched.id}:{minute}"
        try:
            val = _redis.incr(rkey)
            if val == 1:
                # expire after 70 seconds to cover clock skew
                _redis.expire(rkey, 70)
            if val > limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            return matched
        except HTTPException:
            raise
        except Exception:
            # fallback to DB if Redis fails
            pass

    now = datetime.utcnow().replace(second=0, microsecond=0)
    # find or create usage row
    us = session.exec(select(ApiKeyUsage).where(ApiKeyUsage.api_key_id == matched.id, ApiKeyUsage.minute_start == now)).first()
    if us:
        if us.count >= limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        us.count = us.count + 1
        session.add(us)
        session.commit()
    else:
        us = ApiKeyUsage(api_key_id=matched.id, minute_start=now, count=1)
        session.add(us)
        session.commit()

    return matched


def get_current_user(authorization: Optional[str] = Header(None), session: Session = Depends(get_session)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(None, 1)[1].strip()
    ut = session.exec(select(UserToken).where(UserToken.token == token)).first()
    if not ut:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if ut.expires_at and ut.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    user = session.get(User, ut.user_id)
    return user


def require_project_role(project_id: int, role: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> ProjectMembership:
    pm = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == current_user.id, ProjectMembership.project_id == project_id)).first()
    if not pm:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
    if role == 'owner' and pm.role != 'owner':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")
    return pm

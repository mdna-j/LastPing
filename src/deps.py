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

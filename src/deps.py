from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session

from .db import get_session
from .models import Project
from .security import verify_api_key


def _extract_api_key(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if authorization:
        if authorization.lower().startswith("bearer "):
            return authorization.split(None, 1)[1].strip()
    if x_api_key:
        return x_api_key
    return None


def require_project_api_key(project_id: int, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)) -> Project:
    """Dependency that verifies an API key belongs to the requested project.

    Looks for `Authorization: Bearer <key>` or `X-API-KEY` header.
    Raises 401 if no key provided, 403 if invalid or doesn't match project.
    Returns the project model when OK.
    """
    key = _extract_api_key(authorization, x_api_key)
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    # load project by id and verify the provided key against stored hash or legacy plain field
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Prefer hashed verification if available
    if getattr(project, "api_key_hash", None):
        if verify_api_key(key, project.api_key_hash):
            return project
        # fallthrough to legacy plain comparison for compatibility

    if getattr(project, "api_key", None) and project.api_key == key:
        return project

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from .db import get_session
from .models import Project


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

    project = session.exec(select(Project).where(Project.api_key == key)).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    if project.id != project_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key does not belong to this project")

    return project

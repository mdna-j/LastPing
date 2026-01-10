from datetime import datetime
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from ..db import get_session
from ..models import Project


router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel:=None):
    pass


try:
    # avoid Pydantic import clash in older environments
    from pydantic import BaseModel
except Exception:  # pragma: no cover - fallback
    from sqlmodel import SQLModel as BaseModel


class ProjectCreate(BaseModel):
    name: str


class ProjectRead(BaseModel):
    id: int
    name: str
    created_at: datetime


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Depends(get_session)):
    """Create a new project and return the project and API key."""
    api_key = secrets.token_urlsafe(32)
    project = Project(name=payload.name, api_key=api_key)
    session.add(project)
    session.commit()
    session.refresh(project)
    return {"project": ProjectRead.from_orm(project), "api_key": api_key}


@router.get("/", response_model=list[ProjectRead])
def list_projects(session: Depends(get_session)):
    projects = session.exec(select(Project)).all()
    return [ProjectRead.from_orm(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, session: Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRead.from_orm(project)


@router.post("/{project_id}/rotate-key")
def rotate_api_key(project_id: int, session: Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    new_key = secrets.token_urlsafe(32)
    project.api_key = new_key
    session.add(project)
    session.commit()
    session.refresh(project)
    return {"api_key": new_key}

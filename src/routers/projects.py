from datetime import datetime
import secrets
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select, Session

from ..db import get_session
from ..models import Project as ProjectModel


router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str


class ProjectRead(BaseModel):
    id: int
    name: str
    created_at: datetime

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    """Create a new project and return the project and API key."""
    from ..security import generate_api_key, hash_api_key

    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    # store both plaintext (legacy) and hash for compatibility
    project = ProjectModel(name=payload.name, api_key=api_key, api_key_hash=api_key_hash)
    session.add(project)
    session.commit()
    session.refresh(project)
    return {"project": ProjectRead.from_orm(project), "api_key": api_key}


@router.get("/", response_model=List[ProjectRead])
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(ProjectModel)).all()
    return [ProjectRead.from_orm(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRead.from_orm(project)


@router.post("/{project_id}/rotate-key")
def rotate_api_key(project_id: int, session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from ..security import generate_api_key, hash_api_key

    new_key = generate_api_key()
    project.api_key = new_key
    project.api_key_hash = hash_api_key(new_key)
    session.add(project)
    session.commit()
    session.refresh(project)
    return {"api_key": new_key}

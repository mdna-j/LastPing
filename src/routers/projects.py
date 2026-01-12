from datetime import datetime
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel
from sqlmodel import select, Session

from ..db import get_session
from ..models import Project as ProjectModel
from ..security import generate_api_key, hash_api_key
import os


router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    owner_email: Optional[str] = None


class ProjectRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    pagerduty_integration_key: Optional[str] = None
    generic_webhook_url: Optional[str] = None

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    """Create a new project and return the project and API key."""
    from ..security import generate_api_key, hash_api_key

    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    # store only the hash; return plaintext to caller
    project = ProjectModel(name=payload.name, api_key_hash=api_key_hash, owner_email=payload.owner_email)
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


class WebhookUpdate(BaseModel):
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    pagerduty_integration_key: Optional[str] = None
    generic_webhook_url: Optional[str] = None


@router.get("/{project_id}/webhooks", response_model=WebhookUpdate)
def get_project_webhooks(project_id: int, session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return WebhookUpdate(
        discord_webhook_url=project.discord_webhook_url,
        slack_webhook_url=project.slack_webhook_url,
        pagerduty_integration_key=project.pagerduty_integration_key,
        generic_webhook_url=project.generic_webhook_url,
    )


@router.post("/{project_id}/webhooks", response_model=WebhookUpdate)
def update_project_webhooks(project_id: int, payload: WebhookUpdate, session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.discord_webhook_url = payload.discord_webhook_url
    project.slack_webhook_url = payload.slack_webhook_url
    project.pagerduty_integration_key = payload.pagerduty_integration_key
    project.generic_webhook_url = payload.generic_webhook_url
    session.add(project)
    session.commit()
    session.refresh(project)
    return payload


@router.post("/{project_id}/rotate-key")
def rotate_api_key(project_id: int, session: Session = Depends(get_session)):
    project = session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    new_key = generate_api_key()
    project.api_key_hash = hash_api_key(new_key)
    # email the new key to owner if configured
    from ..alerts import send_email
    if getattr(project, 'owner_email', None):
        subject = f"[LastPing] API key rotated for project {project.name}"
        body = f"A new API key was generated for project {project.name}:\n\n{new_key}\n\nStore this securely; it will not be shown again."
        try:
            send_email(subject, body, to=project.owner_email)
        except Exception:
            pass
    session.add(project)
    session.commit()
    session.refresh(project)
    return {"api_key": new_key}


@router.post('/rotate-all-keys')
def rotate_all_keys(x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Admin endpoint to rotate API keys for all projects.

    Protected by `ADMIN_TOKEN` env var. Supply the admin token in header `X-ADMIN-TOKEN`.
    Returns mapping of project id -> new plaintext key.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token:
        raise HTTPException(status_code=403, detail='Admin endpoint not enabled')
    if x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail='Invalid admin token')

    projects = session.exec(select(ProjectModel)).all()
    result = {}
    for p in projects:
        new_key = generate_api_key()
        p.api_key_hash = hash_api_key(new_key)
        session.add(p)
        result[p.id] = new_key
        # email rotated key to owner when available
        try:
            if getattr(p, 'owner_email', None):
                from ..alerts import send_email
                subj = f"[LastPing] API key rotated for project {p.name}"
                body = f"A new API key was generated for project {p.name}:\n\n{new_key}\n\nStore this securely; it will not be shown again."
                send_email(subj, body, to=p.owner_email)
        except Exception:
            pass
    session.commit()
    return result

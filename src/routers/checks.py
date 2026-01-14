"""
Checks CRUD routes.

Create and list monitoring checks for a project. Checks may be
heartbeat-based or HTTP checks; the worker interprets check fields to
drive scheduling and detection logic.
"""

from typing import List, Optional
from datetime import datetime
from pydantic import Field

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, CheckType, CheckStatus, Project
from ..deps import require_project_api_key


router = APIRouter(prefix="/projects/{project_id}/checks", tags=["checks"])


class CheckCreate(BaseModel):
    name: str
    type: Optional[str] = CheckType.HEARTBEAT
    expected_interval: Optional[int] = 600
    grace_period: Optional[int] = 600
    url: Optional[str] = None
    timeout: Optional[int] = 5
    retries: Optional[int] = 1


class CheckRead(BaseModel):
    id: int
    project_id: int
    name: str
    type: str
    status: str
    last_ping: Optional[datetime]
    maintenance_starts_at: Optional[datetime] = Field(None, example="2026-01-14T12:00:00Z")
    maintenance_ends_at: Optional[datetime] = Field(None, example="2026-01-14T13:00:00Z")

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CheckRead)
def create_check(project_id: int, payload: CheckCreate, _proj: Project = Depends(require_project_api_key), session: Session = Depends(get_session)):
    """Create a check for the given project.

    Names must be unique within a project. HTTP checks should provide a
    `url`; heartbeat checks are created automatically by the heartbeat
    endpoint on first use as well.
    """
    # ensure name uniqueness within project
    existing = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Check with that name already exists")

    check = CheckModel(
        project_id=project_id,
        name=payload.name,
        type=payload.type,
        expected_interval=payload.expected_interval,
        grace_period=payload.grace_period,
        url=payload.url,
        timeout=payload.timeout,
        retries=payload.retries,
        status=CheckStatus.UP,
    )
    session.add(check)
    session.commit()
    session.refresh(check)
    return check


@router.get("/", response_model=List[CheckRead])
def list_checks(project_id: int, session: Session = Depends(get_session)):
    """List checks for a project (minimal visibility endpoint)."""
    checks = session.exec(select(CheckModel).where(CheckModel.project_id == project_id)).all()
    return checks


@router.get("/{check_id}", response_model=CheckRead)
def get_check(project_id: int, check_id: int, session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return check


class MaintenanceWindow(BaseModel):
    maintenance_starts_at: Optional[datetime] = Field(None, example="2026-01-14T12:00:00Z")
    maintenance_ends_at: Optional[datetime] = Field(None, example="2026-01-14T13:00:00Z")


@router.get("/{check_id}/maintenance", response_model=MaintenanceWindow, summary="Get check maintenance window", description="Return the check's maintenance window if set.")
def get_check_maintenance(project_id: int, check_id: int, session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return MaintenanceWindow(maintenance_starts_at=check.maintenance_starts_at, maintenance_ends_at=check.maintenance_ends_at)


@router.post("/{check_id}/maintenance", response_model=MaintenanceWindow, summary="Set check maintenance window", description="Set or clear a maintenance window for the check. Requires project API key.")
def set_check_maintenance(project_id: int, check_id: int, payload: MaintenanceWindow, _proj: Project = Depends(require_project_api_key), session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    check.maintenance_starts_at = payload.maintenance_starts_at
    check.maintenance_ends_at = payload.maintenance_ends_at
    session.add(check)
    session.commit()
    session.refresh(check)
    return payload

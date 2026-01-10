from typing import List, Optional
from datetime import datetime

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

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CheckRead)
def create_check(project_id: int, payload: CheckCreate, _proj: Project = Depends(require_project_api_key), session: Session = Depends(get_session)):
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
    checks = session.exec(select(CheckModel).where(CheckModel.project_id == project_id)).all()
    return checks


@router.get("/{check_id}", response_model=CheckRead)
def get_check(project_id: int, check_id: int, session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return check

from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event as EventModel
from ..deps import require_project_api_key


router = APIRouter(prefix="/projects/{project_id}", tags=["alerts"])


class EventRead(BaseModel):
    id: int
    check_id: int
    project_id: int
    event_type: str
    message: str
    created_at: datetime

    class Config:
        orm_mode = True


@router.get("/alerts", response_model=List[EventRead])
def list_project_alerts(project_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj=Depends(require_project_api_key)):
    events = session.exec(select(EventModel).where(EventModel.project_id == project_id).order_by(EventModel.created_at.desc())).all()
    return events


@router.get("/checks/{check_id}/alerts", response_model=List[EventRead])
def list_check_alerts(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), session: Session = Depends(get_session), _proj=Depends(require_project_api_key)):
    events = session.exec(select(EventModel).where(EventModel.project_id == project_id, EventModel.check_id == check_id).order_by(EventModel.created_at.desc())).all()
    return events

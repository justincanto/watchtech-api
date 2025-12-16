from content import service
from fastapi import APIRouter, Depends, HTTPException
from typing import List
import uuid
from sqlalchemy.orm import Session

from db.database import get_db
from content import schemas
from auth.service import get_current_user
from db import models

router = APIRouter(
    tags=["content"],
    responses={404: {"description": "Not found"}},
)

@router.get("/", response_model=List[schemas.Content])
def get_contents(
    limit: int = 12,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.get_contents(db, current_user.id, limit, offset)


@router.get("/{content_id}", response_model=schemas.Content)
def get_content_by_id(
    content_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    content = service.get_user_content_by_id(db, content_id, current_user.id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    return content

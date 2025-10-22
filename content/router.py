from content import service
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Union
import uuid
from sqlalchemy.orm import Session

from db.database import get_db
from content import schemas
from auth.service import get_current_user
from auth.schemas import User
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

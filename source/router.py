from source import service, schemas
from fastapi import APIRouter, Depends, HTTPException
import uuid
from sqlalchemy.orm import Session

from db.database import get_db
from auth.service import get_current_user
from auth.schemas import User

router = APIRouter(
    tags=["source"],
    responses={404: {"description": "Not found"}},
)

@router.get("/{source_id}", response_model=schemas.Source)
def get_source(
    source_id: uuid.UUID, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """Get a specific source by ID with its 12 most recent contents"""
    return service.get_source(db=db, source_id=source_id)

@router.get("/", response_model=schemas.UserSources)
def get_user_sources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all sources for the current user"""
    return {"sources": current_user.sources}


@router.put("/", response_model=schemas.UserSources)
def update_user_sources(
    sources_data: schemas.UserSourcesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update the sources for the current user"""
    sources_dict = [
        {
            "type": source.type,
            "url": str(source.url)
        }
        for source in sources_data.sources
    ]
    sources = service.update_user_sources(db=db, user_id=current_user.id, sources_data=sources_dict)
    return {"sources": sources}
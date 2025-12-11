from sqlalchemy.orm import Session
import uuid
from typing import List, Dict, Any, Callable
from db import models
from extractors.youtube import scrap_video
from extractors.medium import scrap_article
from extractors.dev_to import scrap_article as scrap_dev_to_article
from fastapi import HTTPException
from tasks.content import process_content_task

SOURCE_TYPE_CONTENT_EXTRACTORS: Dict[models.SourceType, Callable[[str], Dict[str, Any]]] = {
    models.SourceType.YOUTUBE: scrap_video,
    models.SourceType.MEDIUM: scrap_article,
    models.SourceType.DEV_TO: scrap_dev_to_article,
}


def queue_content_processing(
    db: Session, 
    url: str, 
    source: models.Source
) -> models.Content:
    """
    Queue content for async processing via Celery.
    Creates a pending content record and dispatches task.
    
    Args:
        db: Database session
        url: Content URL to process
        source: The source this content belongs to
        
    Returns:
        The created Content model (in PENDING status)
    """    
    db_content = models.Content(
        title="Processing...",
        url=url,
        source_id=source.id,
        status=models.ContentStatus.PENDING,
        published_at=None,
    )
    db.add(db_content)
    db.commit()
    db.refresh(db_content)
    
    task = process_content_task.delay(
        url=url,
        source_type=source.type.value,
        source_id=str(source.id),
    )
    
    db_content.task_id = task.id
    db.commit()
    
    return db_content

def get_contents(db: Session, user_id: uuid.UUID, limit: int = 12, offset: int = 0) -> List[models.Content]:
    """Get paginated content from all sources a user is subscribed to, optionally filtered by categories"""
    source_ids = (
        db.query(models.UserSource.source_id)
        .filter(models.UserSource.user_id == user_id)
        .all()
    )
    
    source_ids = [source_id for (source_id,) in source_ids]
    
    if not source_ids:
        return []
    
    contents = (
        db.query(models.Content).
        join(models.Source).
        filter(models.Source.id.in_(source_ids))
        .order_by(models.Content.published_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    
    return contents

def retrieve_content_for_source(
    db: Session, 
    source: models.Source, 
    url: str,
) -> models.Content:
    """
    Retrieve a content URL specifically for a given source type and source.

    This avoids an extra DB lookup of the source by publisher_url and ties the
    content directly to the provided source.
    
    Args:
        db: Database session
        source: The source model
        url: Content URL
        
    Returns:
        Content model (may be in PENDING status if async_mode=True)
    """
    existing = db.query(models.Content).filter(models.Content.url == url).first()
    if existing:
        return existing
    
    return queue_content_processing(db, url, source)


def get_content_by_id(db: Session, content_id: uuid.UUID) -> models.Content:
    """Get a content by its ID"""
    return db.query(models.Content).filter(models.Content.id == content_id).first()

def get_user_content_by_id(db: Session, content_id: uuid.UUID, user_id: uuid.UUID) -> models.Content:
    """Get a content by its ID for a specific user"""
    content = get_content_by_id(db, content_id)
    if not content:
        return None
    # Ensure the content belongs to one of the user's sources
    user_source_ids = [sid for (sid,) in db.query(models.UserSource.source_id).filter(models.UserSource.user_id == user_id).all()]
    if content.source_id not in user_source_ids:
        return None
    return content
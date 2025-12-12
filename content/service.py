from sqlalchemy.orm import Session
import uuid
from typing import List
from db import models
from tasks.content import process_content_task


def queue_content_processing(
    db: Session, 
    source: models.Source,
    url: str, 
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
    existing = db.query(models.Content).filter(models.Content.url == url).first()
    if existing:
        return existing
        
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
    """Get paginated content from all sources a user is subscribed to, only returns fully processed content"""
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
        filter(
            models.Source.id.in_(source_ids),
            models.Content.status == models.ContentStatus.COMPLETED
        )
        .order_by(models.Content.published_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    
    return contents

def get_user_content_by_id(db: Session, content_id: uuid.UUID, user_id: uuid.UUID) -> models.Content:
    """Get a content by its ID for a specific user. Returns None if content is not fully processed."""
    content = db.query(models.Content).filter(
        models.Content.id == content_id,
        models.Content.status == models.ContentStatus.COMPLETED,
    ).first()

    if not content:
        return None
        
    user_source_ids = [sid for (sid,) in db.query(models.UserSource.source_id).filter(models.UserSource.user_id == user_id).all()]
    if content.source_id not in user_source_ids:
        return None
        
    return content
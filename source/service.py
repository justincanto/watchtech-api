import json
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from db import models
from typing import Optional, Tuple, List
import uuid
from fastapi import HTTPException
from subscriptions.youtube import subscribe_channel, unsubscribe_channel
from utils.redis_client import store_batch_sources_sync



def get_or_create_source(db: Session, type: models.SourceType, url: str) -> models.Source:
    """
    Get or create a source with the given type and URL.
    
    If a source with the same URL already exists, return it.
    Otherwise, create a new source in PENDING status (to be processed by Celery task).
    
    Returns:
        The source (existing or newly created)
    """
    existing_source = db.query(models.Source).filter(
        models.Source.url == url
    ).first()
    
    if existing_source:
        return existing_source
    
    # Create source in PENDING status - actual data will be fetched by task
    new_source = models.Source(
        type=type,
        url=url,
        name=None,  # Will be set by task
        original_id=None,  # Will be set by task
        status=models.SourceStatus.PENDING,
    )

    db.add(new_source)
    db.commit()
    db.refresh(new_source)

    return new_source

def get_source(db: Session, source_id: uuid.UUID, limit_contents: int = 12) -> Optional[models.Source]:
    """
    Get a source by ID and include its most recent completed contents.
    Only returns contents that are fully processed.
    """
    source = db.query(models.Source).filter(
        models.Source.id == source_id, 
        models.Source.status == models.SourceStatus.COMPLETED
    ).first()
    
    if source:
        recent_contents = (
            db.query(models.Content)
            .filter(
                models.Content.source_id == source_id,
                models.Content.status == models.ContentStatus.COMPLETED
            )
            .order_by(models.Content.created_at.desc())
            .limit(limit_contents)
            .all()
        )
        
        # Use set_committed_value to set the contents without marking the
        # relationship as dirty. Direct assignment (source.contents = ...) 
        # would cause SQLAlchemy to try to disassociate excluded contents
        # by setting their source_id to NULL, which fails the NOT NULL constraint.
        set_committed_value(source, 'contents', recent_contents)
    
    return source 

def get_user_sources(db: Session, user_id: uuid.UUID) -> List[models.Source]:
    """Get all sources for a specific user"""
    sources = (db.query(models.Source)
        .join(models.UserSource, models.Source.id == models.UserSource.source_id)
        .filter(models.UserSource.user_id == user_id)
        .all())
     
    return sources

def update_user_sources(db: Session, user_id: uuid.UUID, sources_data: List[dict]) -> Tuple[str, List[models.Source], List[str]]:
    """
    Update the sources for a user with async processing.
    
    Returns:
        Tuple of (batch_id, sources list, new_source_ids)
        - batch_id: Can be used to track progress via SSE endpoint
        - sources: List of source models (some may be in PENDING status)
        - new_source_ids: List of source IDs that need processing (for frontend tracking)
    """
    try:
        # Generate batch ID for progress tracking
        batch_id = str(uuid.uuid4())
        
        existing_user_sources = db.query(models.UserSource).filter(
            models.UserSource.user_id == user_id
        ).all()
        
        existing_user_source_ids = {us.source_id for us in existing_user_sources}
        
        new_source_ids = set()
        sources_to_process = []
        source_ids_to_process = []
        
        for source_data in sources_data:    
            source = get_or_create_source(
                db=db, 
                type=source_data["type"], 
                url=source_data["url"]
            )
            
            new_source_ids.add(source.id)

            if source.id not in existing_user_source_ids:
                user_source = models.UserSource(
                    user_id=user_id,
                    source_id=source.id 
                )
                db.add(user_source)
            
            if source.status in [models.SourceStatus.PENDING, models.SourceStatus.FAILED]:
                sources_to_process.append(source)
                source_ids_to_process.append(str(source.id))
        
            if source.type == models.SourceType.YOUTUBE and source.id not in existing_user_source_ids:
                if not db.query(models.YouTubeSubscription).filter(models.YouTubeSubscription.source_id == source.id).first():
                    subscribe_channel(db, source)

        sources_to_remove = existing_user_source_ids - new_source_ids
        if sources_to_remove:
            db.query(models.UserSource).filter(
                models.UserSource.user_id == user_id,
                models.UserSource.source_id.in_(sources_to_remove)
            ).delete(synchronize_session=False)
        
        db.commit()
        
        store_batch_sources_sync(batch_id, source_ids_to_process)
        
        # Import here to avoid circular dependency with tasks.source
        from tasks.source import process_source_task
        for source in sources_to_process:
            process_source_task.delay(source_id=str(source.id))
        
        updated_sources = get_user_sources(db=db, user_id=user_id)
        
        clean_up_orphan_subscriptions(db)

        return batch_id, updated_sources, source_ids_to_process
    
    except Exception as e: 
        print(f"Error updating user sources: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating user sources: {str(e)}")

def clean_up_orphan_subscriptions(db: Session) -> None:
    orphan_source_ids = (
        db.query(models.YouTubeSubscription.source_id)
        .join(models.Source, models.YouTubeSubscription.source_id == models.Source.id)
        .outerjoin(models.UserSource, models.Source.id == models.UserSource.source_id)
        .filter(models.UserSource.source_id == None)
        .all()
    )

    orphan_source_ids = [sid for (sid,) in orphan_source_ids]
    if orphan_source_ids:
        for source in db.query(models.Source).filter(models.Source.id.in_(orphan_source_ids)).all():
            try:
                unsubscribe_channel(db, source)
            except Exception as e:
                print(f"Failed to unsubscribe YouTube PubSub for source {source.id}: {e}")
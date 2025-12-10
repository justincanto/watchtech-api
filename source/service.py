from sqlalchemy.orm import Session
from sqlalchemy import func
from db import models
from typing import Optional, Dict, Callable, Any, Tuple, List
from extractors.youtube import get_channel_data, get_youtube_channel_videos
from extractors.medium import get_author_data, get_medium_author_articles
from extractors.dev_to import get_author_data as get_dev_to_author_data, get_dev_to_author_articles
import uuid
from fastapi import HTTPException
from content import service as content_service
from subscriptions.youtube import subscribe_channel, unsubscribe_channel

# Mapping from source type to extractor function
SOURCE_TYPE_AUTHOR_DATA_EXTRACTORS: Dict[models.SourceType, Callable[[str], Dict[str, Any]]] = {
    models.SourceType.YOUTUBE: get_channel_data, 
    models.SourceType.MEDIUM: get_author_data,
    models.SourceType.DEV_TO: get_dev_to_author_data,
}

SOURCE_TYPE_URL_EXTRACTORS: Dict[models.SourceType, Callable[[str, int], List[str]]] = {
    models.SourceType.YOUTUBE: get_youtube_channel_videos,
    models.SourceType.MEDIUM: get_medium_author_articles,
    models.SourceType.DEV_TO: get_dev_to_author_articles,
}


def get_or_create_source(db: Session, type: models.SourceType, url: str) -> Tuple[models.Source, bool]:
    """
    Create a new source with the given type and URL.
    If a source with the same type and URL already exists, return it instead.
    """
    existing_source = db.query(models.Source).filter(
        models.Source.type == type,
        models.Source.url == url
    ).first()
    
    if existing_source:
        return existing_source
    
    extractor_func = SOURCE_TYPE_AUTHOR_DATA_EXTRACTORS[type]
    data = extractor_func(url)
    name = data["name"]
    original_id = data["id"]
    
    new_source = models.Source(
        type=type,
        url=url,
        name=name,
        original_id=original_id
    )

    db.add(new_source)
    db.commit()

    if new_source.type == models.SourceType.YOUTUBE:
        try:
            subscribe_channel(db, new_source)
        except Exception as e:
            print(f"Failed to subscribe to YouTube PubSub for source {new_source.id}: {e}")

    ingest_source_latest_contents(db, new_source)

    db.refresh(new_source)

    return new_source


def ingest_source_latest_contents(
    db: Session, 
    source: models.Source, 
    limit: int = 1,
) -> List[models.Content]:
    """
    Ingest the latest contents from a source.
    
    Args:
        db: Database session
        source: The source to ingest content from
        limit: Maximum number of contents to ingest
        
    Returns:
        List of Content models (may be in PENDING status if async_mode=True)
    """
    contents = []
    try:
        content_urls = SOURCE_TYPE_URL_EXTRACTORS[source.type](source.url, limit)
        for url in content_urls:
            content = content_service.retrieve_content_for_source(
                db, source, url
            )
            contents.append(content)
            
    except Exception as e:
        print(f"Error processing content from source {source.id}: {str(e)}")
        return contents
    
    return contents

def get_source(db: Session, source_id: uuid.UUID, limit_contents: int = 12) -> Optional[models.Source]:
    """
    Get a source by ID and include its most recent contents.
    """
    source = db.query(models.Source).filter(models.Source.id == source_id).first()
    
    if source:
        recent_contents = (
            db.query(models.Content)
            .filter(models.Content.source_id == source_id)
            .order_by(models.Content.created_at.desc())
            .limit(limit_contents)
            .all()
        )
        
        # Add the contents to the source
        # Note: This won't override the existing relationship,
        # it just replaces the loaded contents with our limited set
        source.contents = recent_contents

    
    return source 

def get_user_sources(db: Session, user_id: uuid.UUID) -> List[models.Source]:
    """Get all sources for a specific user"""
    sources = (db.query(models.Source)
        .join(models.UserSource, models.Source.id == models.UserSource.source_id)
        .filter(models.UserSource.user_id == user_id)
        .all())
     
    return sources

def update_user_sources(db: Session, user_id: uuid.UUID, sources_data: List[dict]) -> List[models.Source]:
    """Update the sources for a user"""
    try:
        existing_user_sources = db.query(models.UserSource).filter(
            models.UserSource.user_id == user_id
        ).all()
        
        existing_source_ids = {us.source_id for us in existing_user_sources}
        
        new_source_ids = set()
        
        for source_data in sources_data:    
            source = get_or_create_source(
                db=db, 
                type=source_data["type"], 
                url=source_data["url"]
            )
            
            new_source_ids.add(source.id)

            if source.id not in existing_source_ids:
                user_source = models.UserSource(
                    user_id=user_id,
                    source_id=source.id 
                )
                db.add(user_source)
        
        sources_to_remove = existing_source_ids - new_source_ids
        if sources_to_remove:
            db.query(models.UserSource).filter(
                models.UserSource.user_id == user_id,
                models.UserSource.source_id.in_(sources_to_remove)
            ).delete(synchronize_session=False)
        
        updated_sources = get_user_sources(db=db, user_id=user_id)

        db.commit()
        
        clean_up_orphan_subscriptions(db)

        return updated_sources
    
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
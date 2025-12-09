from sqlalchemy.orm import Session
import uuid
from typing import Optional, List, Dict, Any, Callable
from db import models
from extractors.youtube import scrap_video
from extractors.medium import scrap_article
from extractors.dev_to import scrap_article as scrap_dev_to_article
from agents.summarizer import summarize_content
from fastapi import HTTPException

SOURCE_TYPE_CONTENT_EXTRACTORS: Dict[models.SourceType, Callable[[str], Dict[str, Any]]] = {
    models.SourceType.YOUTUBE: scrap_video,
    models.SourceType.MEDIUM: scrap_article,
    models.SourceType.DEV_TO: scrap_dev_to_article,
}

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

def retrieve_content(db: Session, url: str, source_type: models.SourceType) -> models.Content:
    """Retrieve a content, summarize it and save it to the database"""
    content_data = SOURCE_TYPE_CONTENT_EXTRACTORS[source_type](url)
    summary = summarize_content(content_data["content"])

    content_source = db.query(models.Source).filter(
        models.Source.type == source_type,
        models.Source.original_id == content_data["publisher_id"]
    ).first()

    if not content_source:
        raise HTTPException(status_code=404, detail="Source not found")

    db_content = models.Content(
        title=content_data["title"],
        transcript=content_data["content"],
        summary=summary,
        url=url,
        source_id=content_source.id,
        published_at=content_data["published_at"]
    )
    
    db.add(db_content)
    db.commit()
    db.refresh(db_content)

    return db_content


def retrieve_content_for_source(db: Session, source: models.Source, url: str) -> models.Content:
    """Retrieve a content URL specifically for a given source type and source.

    This avoids an extra DB lookup of the source by publisher_url and ties the
    content directly to the provided source.
    """
    content_data = SOURCE_TYPE_CONTENT_EXTRACTORS[source.type](url)
    summary = summarize_content(content_data["content"])

    db_content = models.Content(
        title=content_data["title"],
        summary=summary,
        url=url,
        source_id=source.id,
        published_at=content_data["published_at"],
    )

    db.add(db_content)
    db.commit()
    db.refresh(db_content)

    return db_content

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
    print(user_source_ids)
    if content.source_id not in user_source_ids:
        return None
    return content
import uuid
from typing import Optional
from celery.utils.log import get_task_logger

from celery_app import celery_app
from db.database import SessionLocal
from db import models
from extractors.youtube import scrap_video
from extractors.medium import scrap_article
from extractors.dev_to import scrap_article as scrap_dev_to_article
from agents.summarizer import summarize_content
from utils.redis_client import (
    add_success_content,
    add_failed_content,
    move_failed_to_success,
    publish_source_progress,
)

logger = get_task_logger(__name__)

SOURCE_TYPE_EXTRACTORS = {
    "youtube": scrap_video,
    "medium": scrap_article,
    "dev_to": scrap_dev_to_article,
}

@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def process_content_task(
    self,
    url: str,
    source_type: str,
    source_id: str,
) -> Optional[str]:
    """
    Combined task to retrieve content, generate summary, and save to database.
    
    This is the main task that handles the full content processing pipeline.
    The Content record should already exist (created by queue_content_processing),
    this task updates it with extracted data and summary.
    
    Args:
        url: The URL of the content to process
        source_type: The type of source (youtube, medium, dev_to)
        source_id: UUID of the source this content belongs to
        
    Returns:
        The UUID of the created content, or None if processing failed
    """
    logger.info(f"Processing content: {url} for source {source_id}")
    
    db = SessionLocal()
    try:
        db_content = db.query(models.Content).filter(models.Content.url == url).first()
        
        if db_content and db_content.status not in [models.ContentStatus.PENDING, models.ContentStatus.FAILED]:
            logger.info(f"Content already completed or being processed: {db_content.id}")
            return str(db_content.id)

        is_retry = db_content and db_content.status == models.ContentStatus.FAILED
        
        content_id = db_content.id
        logger.info(f"Processing content record: {content_id}")
        
        db_content.status = models.ContentStatus.EXTRACTING
        db.commit()
        
        extractor = SOURCE_TYPE_EXTRACTORS.get(source_type)
        
        try:
            content_data = extractor(url)
        except Exception as e:
            db_content.status = models.ContentStatus.FAILED
            db_content.error_message = f"Extraction failed: {str(e)}"
            db.commit()
            
            add_failed_content(source_id, str(content_id))
            raise
        
        db_content.title = content_data["title"]
        db_content.transcript = content_data.get("content")
        db_content.description = content_data.get("description")
        db_content.published_at = content_data.get("published_at")
        db_content.status = models.ContentStatus.SUMMARIZING
        db.commit()
        
        logger.info(f"Content extracted: {db_content.title}")
        
        try:
            summary = summarize_content(content_data.get("content", ""))
            db_content.summary = summary
            db_content.status = models.ContentStatus.COMPLETED
            db_content.error_message = None
            db.commit()
            logger.info(f"Content processing completed: {content_id}")
            
        except Exception as e:
            db_content.status = models.ContentStatus.FAILED
            db_content.error_message = f"Summarization failed: {str(e)}"
            db.commit()
            
            add_failed_content(source_id, str(content_id))
            raise
        
        if is_retry:
            tracking = move_failed_to_success(source_id, str(content_id))
        else:
            tracking = add_success_content(source_id, str(content_id))
        
        if tracking and tracking["is_complete"]:
            source = db.query(models.Source).filter(
                models.Source.id == uuid.UUID(source_id)
            ).first()
            
            if source and source.status == models.SourceStatus.INGESTING_CONTENT:
                source.status = models.SourceStatus.COMPLETED
                source.error_message = None
                db.commit()
                
                publish_source_progress(
                    source_id=source_id,
                    status=models.SourceStatus.COMPLETED.value,
                    progress=1.0,
                    message="Source processing completed",
                    source_url=tracking["source_url"],
                    source_name=tracking["source_name"],
                    content_total=tracking["content_total"],
                    success_content_ids=tracking["success_content_ids"],
                    failed_content_ids=tracking["failed_content_ids"],
                    has_warning=tracking["has_warning"],
                    is_complete=tracking["is_complete"],
                )
                logger.info(f"Source {source_id} marked as completed after all content processed")
        
        return str(content_id)
        
    except Exception as e:
        logger.error(f"Error processing content {url}: {e}")
        db.rollback()
        raise
    finally:
        db.close()
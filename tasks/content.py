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
from utils.redis_client import increment_source_content_processed, publish_source_progress, publish_content_processed

logger = get_task_logger(__name__)

# Mapping from source type string to extractor function
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
        # Find existing content record (should be created by queue_content_processing)
        db_content = db.query(models.Content).filter(models.Content.url == url).first()
        
        if db_content and db_content.status == models.ContentStatus.COMPLETED:
            logger.info(f"Content already completed: {db_content.id}")
            return str(db_content.id)
        
        # If no record exists, create one (fallback for direct task calls)
        if not db_content:
            source = db.query(models.Source).filter(
                models.Source.id == uuid.UUID(source_id)
            ).first()
            
            if not source:
                logger.error(f"Source not found: {source_id}")
                return None
            
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
        
        content_id = db_content.id
        logger.info(f"Processing content record: {content_id}")
        
        # Step 1: Extract content data
        db_content.status = models.ContentStatus.EXTRACTING
        db.commit()
        
        extractor = SOURCE_TYPE_EXTRACTORS.get(source_type)
        if not extractor:
            db_content.status = models.ContentStatus.FAILED
            db_content.error_message = f"Unknown source type: {source_type}"
            db.commit()
            raise ValueError(f"Unknown source type: {source_type}")
        
        try:
            content_data = extractor(url)
        except Exception as e:
            db_content.status = models.ContentStatus.FAILED
            db_content.error_message = f"Extraction failed: {str(e)}"
            db.commit()
            raise
        
        # Update with extracted data
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
            
            # Publish content processed event for real-time UI updates
            publish_content_processed(
                content_id=str(content_id),
                source_id=source_id,
                title=db_content.title,
                url=db_content.url,
            )
        except Exception as e:
            db_content.status = models.ContentStatus.FAILED
            db_content.error_message = f"Summarization failed: {str(e)}"
            db.commit()
            raise
        
        tracking = increment_source_content_processed(source_id)
        
        if tracking and (tracking["content_processed"] >= tracking["content_total"]):
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
                    content_processed=tracking["content_processed"],
                )
                logger.info(f"Source {source_id} marked as completed after all content processed")
    
        return str(content_id)
        
    except Exception as e:
        logger.error(f"Error processing content {url}: {e}")
        db.rollback()
        raise
    finally:
        db.close()
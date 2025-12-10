import uuid
from typing import Any, Callable, Dict, List, Optional
from celery.utils.log import get_task_logger

from celery_app import celery_app
from db.database import SessionLocal
from db.models import Source, SourceStatus, SourceType
from extractors.youtube import get_channel_data, get_youtube_channel_videos
from extractors.medium import get_author_data, get_medium_author_articles
from extractors.dev_to import get_author_data as get_dev_to_author_data, get_dev_to_author_articles
from subscriptions.youtube import subscribe_channel
from content import service as content_service
from utils.redis_client import publish_source_progress

logger = get_task_logger(__name__)

# Mapping from source type string to author data extractor
SOURCE_TYPE_AUTHOR_EXTRACTORS: Dict[SourceType, Callable[[str], Dict[str, Any]]] = {
    SourceType.YOUTUBE: get_channel_data,
    SourceType.MEDIUM: get_author_data,
    SourceType.DEV_TO: get_dev_to_author_data,
}

# Mapping from source type string to content URL extractor
SOURCE_TYPE_CONTENT_EXTRACTORS: Dict[SourceType, Callable[[str, int], List[str]]] = {
    SourceType.YOUTUBE: get_youtube_channel_videos,
    SourceType.MEDIUM: get_medium_author_articles,
    SourceType.DEV_TO: get_dev_to_author_articles,
}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def process_source_task(
    self,
    source_id: str,
) -> Optional[str]:
    """
    Process a source: fetch author data and ingest initial content.
    
    This task handles the full source processing pipeline:
    1. Fetch author/channel data from external API
    2. Update source with name and original_id
    3. Subscribe to YouTube PubSub (if applicable)
    4. Ingest initial content
    
    Args:
        source_id: UUID of the source to process
        
    Returns:
        The source ID if successful, None if failed
    """
    logger.info(f"Processing source: {source_id}")
    
    db = SessionLocal()
    try:
        # Get the source
        source = db.query(Source).filter(
            Source.id == uuid.UUID(source_id)
        ).first()
        
        if not source:
            logger.error(f"Source not found: {source_id}")
            return None
        
        # Check if already completed or being processed by another task
        # This handles the deduplication case
        if source.status == SourceStatus.COMPLETED:
            logger.info(f"Source already completed: {source_id}")
            publish_source_progress(
                source_id=source_id,
                status=SourceStatus.COMPLETED.value,
                progress=1.0,
                message="Source already processed",
                source_url=source.url,
                source_name=source.name,
            )
            return source_id
        
        # Check if another task is already processing this source
        if source.status in [SourceStatus.FETCHING_AUTHOR, SourceStatus.INGESTING_CONTENT]:
            logger.info(f"Source already being processed: {source_id}, status: {source.status}")
            # Don't process, let the existing task handle it
            return source_id
        
        # Mark as fetching author data
        source.status = SourceStatus.FETCHING_AUTHOR
        source.error_message = None
        db.commit()
        
        publish_source_progress(
            source_id=source_id,
            status=SourceStatus.FETCHING_AUTHOR.value,
            progress=0.1,
            message="Fetching author data...",
            source_url=source.url,
        )
        
        # Step 1: Fetch author data
        source_type_str = source.type.value
        author_extractor = SOURCE_TYPE_AUTHOR_EXTRACTORS.get(source_type_str)
        
        try:
            author_data = author_extractor(source.url)
            source.name = author_data["name"]
            source.original_id = author_data["id"]
            db.commit()
            
            logger.info(f"Author data fetched for source {source_id}: {source.name}")
            
            publish_source_progress(
                source_id=source_id,
                status=SourceStatus.FETCHING_AUTHOR.value,
                progress=0.3,
                message=f"Author data fetched: {source.name}",
                source_url=source.url,
                source_name=source.name,
            )
        except Exception as e:
            source.status = SourceStatus.FAILED
            source.error_message = f"Failed to fetch author data: {str(e)}"
            db.commit()
            publish_source_progress(
                source_id=source_id,
                status=SourceStatus.FAILED.value,
                progress=0.0,
                message=f"Failed to fetch author data: {str(e)}",
                source_url=source.url,
            )
            raise
        
        # Step 2: Subscribe to YouTube PubSub if applicable
        if source.type == SourceType.YOUTUBE:
            try:
                subscribe_channel(db, source)
                logger.info(f"Subscribed to YouTube PubSub for source {source_id}")
            except Exception as e:
                logger.warning(f"Failed to subscribe to YouTube PubSub for source {source_id}: {e}")
                # Don't fail the task for subscription errors
        
        # Step 3: Ingest initial content
        source.status = SourceStatus.INGESTING_CONTENT
        db.commit()
        
        publish_source_progress(
            source_id=source_id,
            status=SourceStatus.INGESTING_CONTENT.value,
            progress=0.5,
            message="Ingesting initial content...",
            source_url=source.url,
            source_name=source.name,
        )
        
        content_extractor = SOURCE_TYPE_CONTENT_EXTRACTORS.get(source_type_str)
        if content_extractor:
            try:
                content_urls = content_extractor(source.url, 1)  # Ingest 1 initial content
                for url in content_urls:
                    content_service.retrieve_content_for_source(db, source, url)
                    
                logger.info(f"Ingested {len(content_urls)} content(s) for source {source_id}")
                
                publish_source_progress(
                    source_id=source_id,
                    status=SourceStatus.INGESTING_CONTENT.value,
                    progress=0.8,
                    message=f"Ingested {len(content_urls)} content(s)",
                    source_url=source.url,
                    source_name=source.name,
                )
            except Exception as e:
                logger.warning(f"Error ingesting content for source {source_id}: {e}")
                # Don't fail the task for content ingestion errors
        
        # Mark as completed
        source.status = SourceStatus.COMPLETED
        source.error_message = None
        db.commit()
        
        publish_source_progress(
            source_id=source_id,
            status=SourceStatus.COMPLETED.value,
            progress=1.0,
            message="Source processing completed",
            source_url=source.url,
            source_name=source.name,
        )
        
        logger.info(f"Source processing completed: {source_id}")
        return source_id
        
    except Exception as e:
        logger.error(f"Error processing source {source_id}: {e}")
        db.rollback()
        raise
    finally:
        db.close()


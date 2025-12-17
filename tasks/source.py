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
from utils.redis_client import publish_source_progress, init_source_content_tracking

logger = get_task_logger(__name__)

# Mapping from source type string to author data extractor
SOURCE_TYPE_AUTHOR_EXTRACTORS: Dict[SourceType, Callable[[str], Dict[str, Any]]] = {
    SourceType.YOUTUBE.value: get_channel_data,
    SourceType.MEDIUM.value: get_author_data,
    SourceType.DEV_TO.value: get_dev_to_author_data,
}

# Mapping from source type string to content URL extractor
SOURCE_TYPE_CONTENT_EXTRACTORS: Dict[SourceType, Callable[[str, int], List[str]]] = {
    SourceType.YOUTUBE.value: get_youtube_channel_videos,
    SourceType.MEDIUM.value: get_medium_author_articles,
    SourceType.DEV_TO.value: get_dev_to_author_articles,
}

PROGRESS_AUTHOR_START = 0.0
PROGRESS_AUTHOR_END = 0.20
PROGRESS_DISCOVER_START = 0.20
PROGRESS_DISCOVER_END = 0.35
PROGRESS_CONTENT_START = 0.35
PROGRESS_CONTENT_END = 1.0

INITIAL_CONTENT_COUNT = 5


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
    4. Discover and ingest initial content with detailed progress
    
    Progress breakdown:
    - 0-20%: Fetching author data
    - 20-35%: Discovering content URLs
    - 35-100%: Processing content items (incremental)
    
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
        
        if source.status in [SourceStatus.FETCHING_AUTHOR, SourceStatus.INGESTING_CONTENT]:
            logger.info(f"Source already being processed: {source_id}, status: {source.status}")
            # Don't process, let the existing task handle it
            return source_id

        if source.type == SourceType.YOUTUBE:
            try:
                subscribe_channel(db, source)
                logger.info(f"Subscribed to YouTube PubSub for source {source_id}")
            except Exception as e:
                logger.warning(f"Failed to subscribe to YouTube PubSub for source {source_id}: {e}")
                source.status = SourceStatus.FAILED
                source.error_message = f"Failed to subscribe to YouTube PubSub: {str(e)}"
                db.commit()
                publish_source_progress(
                    source_id=source_id,
                    status=SourceStatus.FAILED.value,
                    progress=0.0,
                    message=f"Failed to subscribe to YouTube PubSub: {str(e)}",
                    source_url=source.url,
                )
                raise
        
        source.status = SourceStatus.FETCHING_AUTHOR
        source.error_message = None
        db.commit()
        
        publish_source_progress(
            source_id=source_id,
            status=SourceStatus.FETCHING_AUTHOR.value,
            progress=PROGRESS_AUTHOR_START,
            message="Fetching author data...",
            source_url=source.url,
        )
        
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
                progress=PROGRESS_AUTHOR_END,
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
        
        source.status = SourceStatus.INGESTING_CONTENT
        db.commit()
        
        publish_source_progress(
            source_id=source_id,
            status=SourceStatus.INGESTING_CONTENT.value,
            progress=PROGRESS_DISCOVER_START,
            message="Discovering content...",
            source_url=source.url,
            source_name=source.name,
        )
        
        content_extractor = SOURCE_TYPE_CONTENT_EXTRACTORS.get(source_type_str)
        content_urls = []
        
        if content_extractor:
            try:
                content_urls = content_extractor(source.url, INITIAL_CONTENT_COUNT)
                logger.info(f"Discovered {len(content_urls)} content URLs for source {source_id}")
                
                publish_source_progress(
                    source_id=source_id,
                    status=SourceStatus.INGESTING_CONTENT.value,
                    progress=PROGRESS_DISCOVER_END,
                    message=f"Found {len(content_urls)} content items",
                    source_url=source.url,
                    source_name=source.name,
                    content_total=len(content_urls),
                    success_content_ids=[],
                    failed_content_ids=[],
                    has_warning=False,
                    is_complete=False,
                )
            except Exception as e:
                logger.warning(f"Error discovering content for source {source_id}: {e}")
                # Continue without content - mark as completed
        
        content_total = len(content_urls)
        
        if content_total > 0:
            init_source_content_tracking(
                source_id=source_id,
                content_total=content_total,
                source_url=source.url,
                source_name=source.name,
            )
            
            for url in content_urls:
                try:
                    content_service.queue_content_processing(db, source, url)
                except Exception as e:
                    logger.warning(f"Error queuing content {url} for source {source_id}: {e}")
            
            logger.info(f"Queued {content_total} content(s) for source {source_id}")
            return source_id
        
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
            content_total=0,
            success_content_ids=[],
            failed_content_ids=[],
            has_warning=False,
            is_complete=True,
        )
        
        logger.info(f"Source processing completed (no content): {source_id}")
        return source_id
        
    except Exception as e:
        logger.error(f"Error processing source {source_id}: {e}")
        db.rollback()
        raise
    finally:
        db.close()


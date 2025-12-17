import os
import json
from typing import List, Optional
from contextlib import asynccontextmanager

from db.enums import SourceStatus
import redis
import redis.asyncio as aioredis

# Redis connection URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Batch TTL in seconds (1 hour)
BATCH_TTL = 3600

# Sync Redis client for Celery tasks
_sync_redis_client: Optional[redis.Redis] = None


def get_sync_redis_client() -> redis.Redis:
    """Get or create a synchronous Redis client for Celery tasks."""
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _sync_redis_client


def get_async_redis_client() -> aioredis.Redis:
    """Create a new async Redis client for FastAPI endpoints."""
    return aioredis.from_url(REDIS_URL, decode_responses=True)


@asynccontextmanager
async def get_async_redis():
    """Async context manager for Redis client."""
    client = get_async_redis_client()
    try:
        yield client
    finally:
        await client.aclose()


def store_batch_sources_sync(batch_id: str, source_ids: List[str]) -> None:
    """
    Store the list of source IDs for a batch.
    Used by the source service when creating a batch.
    """
    client = get_sync_redis_client()
    key = f"batch:{batch_id}:sources"
    client.set(key, json.dumps(source_ids), ex=BATCH_TTL)


async def get_batch_sources(batch_id: str) -> Optional[List[str]]:
    """
    Retrieve the list of source IDs for a batch.
    Returns None if batch not found.
    """
    async with get_async_redis() as client:
        key = f"batch:{batch_id}:sources"
        data = await client.get(key)
        if data:
            return json.loads(data)
        return None


def publish_source_progress(
    source_id: str,
    status: str,
    progress: float,
    message: str,
    source_url: Optional[str] = None,
    source_name: Optional[str] = None,
    content_total: Optional[int] = None,
    success_content_ids: Optional[List[str]] = None,
    failed_content_ids: Optional[List[str]] = None,
    has_warning: Optional[bool] = None,
    is_complete: Optional[bool] = None,
) -> None:
    """
    Publish a progress event for a source.
    Used by Celery tasks to notify progress.
    
    Args:
        source_id: UUID of the source
        status: Current status (pending, fetching_author, ingesting_content, completed, failed)
        progress: Progress percentage (0.0 to 1.0)
        message: Human-readable progress message
        source_url: Optional source URL for context
        source_name: Optional source name (once fetched)
        content_total: Optional total number of content items to process
        success_content_ids: Optional list of successfully processed content IDs
        failed_content_ids: Optional list of failed content IDs
        has_warning: Optional flag indicating if there are failures
        is_complete: Optional flag indicating if all content is processed
    """
    client = get_sync_redis_client()
    channel = f"source:{source_id}:progress"
    
    event_data = {
        "source_id": source_id,
        "status": status,
        "progress": progress,
        "message": message,
    }
    
    if source_url:
        event_data["source_url"] = source_url
    if source_name:
        event_data["source_name"] = source_name
    if content_total is not None:
        event_data["content_total"] = content_total
    if success_content_ids is not None:
        event_data["success_content_ids"] = success_content_ids
    if failed_content_ids is not None:
        event_data["failed_content_ids"] = failed_content_ids
    if has_warning is not None:
        event_data["has_warning"] = has_warning
    if is_complete is not None:
        event_data["is_complete"] = is_complete
    
    client.publish(channel, json.dumps(event_data))


PROGRESS_CONTENT_START = 0.35
PROGRESS_CONTENT_END = 1.0


def init_source_content_tracking(
    source_id: str,
    content_total: int,
    source_url: str,
    source_name: str,
) -> None:
    """
    Initialize content tracking state for a source.
    Called by source task after discovering content URLs.
    
    Args:
        source_id: UUID of the source
        content_total: Total number of content items to process
        source_url: Source URL for context in progress events
        source_name: Source name for context in progress events
    """
    client = get_sync_redis_client()
    tracking_key = f"source:{source_id}:content_tracking"
    success_key = f"source:{source_id}:success_ids"
    failed_key = f"source:{source_id}:failed_ids"
    
    tracking_data = {
        "content_total": content_total,
        "source_url": source_url,
        "source_name": source_name,
        "status": SourceStatus.INGESTING_CONTENT.value,
    }
    
    client.set(tracking_key, json.dumps(tracking_data), ex=BATCH_TTL)
    # Clear any existing sets and set TTL
    client.delete(success_key, failed_key)
    # Create empty sets with TTL by adding and removing a dummy value
    # This ensures the keys exist for SMEMBERS calls


def add_success_content(source_id: str, content_id: str) -> Optional[dict]:
    """
    Add a content ID to the success set for a source and publish progress.
    Called by content task when processing completes successfully.
    
    Uses atomic SADD to safely handle concurrent updates from multiple workers.
    
    Args:
        source_id: UUID of the source
        content_id: UUID of the content that succeeded
        
    Returns:
        Updated tracking data dict, or None if tracking not found
    """
    client = get_sync_redis_client()
    success_key = f"source:{source_id}:success_ids"
    
    # Add to success set (atomic operation)
    client.sadd(success_key, content_id)
    client.expire(success_key, BATCH_TTL)
    
    return _send_source_ingestion_tracking_update_event(client, source_id)


def add_failed_content(source_id: str, content_id: str) -> Optional[dict]:
    """
    Add a content ID to the failed set for a source and publish progress.
    Called by content task when processing fails.
    
    Uses atomic SADD to safely handle concurrent updates from multiple workers.
    
    Args:
        source_id: UUID of the source
        content_id: UUID of the content that failed
        
    Returns:
        Updated tracking data dict, or None if tracking not found
    """
    client = get_sync_redis_client()
    failed_key = f"source:{source_id}:failed_ids"
    
    # Add to failed set (atomic operation)
    client.sadd(failed_key, content_id)
    client.expire(failed_key, BATCH_TTL)
    
    return _send_source_ingestion_tracking_update_event(client, source_id)


def move_failed_to_success(source_id: str, content_id: str) -> Optional[dict]:
    """
    Move a content ID from the failed set to the success set (for retries).
    Called by content task when a retry succeeds.
    
    Uses atomic SMOVE to safely handle concurrent updates.
    
    Args:
        source_id: UUID of the source
        content_id: UUID of the content that succeeded on retry
        
    Returns:
        Updated tracking data dict, or None if tracking not found
    """
    client = get_sync_redis_client()
    success_key = f"source:{source_id}:success_ids"
    failed_key = f"source:{source_id}:failed_ids"
    
    client.smove(failed_key, success_key, content_id)
    client.expire(success_key, BATCH_TTL)
    client.expire(failed_key, BATCH_TTL)
    
    return _send_source_ingestion_tracking_update_event(client, source_id)


def get_source_content_tracking(source_id: str) -> Optional[dict]:
    """
    Get the current content tracking state for a source.
    
    Args:
        source_id: UUID of the source
        
    Returns:
        Tracking data dict with success_ids, failed_ids, and computed fields,
        or None if tracking not found
    """
    client = get_sync_redis_client()
    return _get_content_tracking_state(client, source_id)

def _send_source_ingestion_tracking_update_event(client: redis.Redis, source_id: str):
    tracking = _get_content_tracking_state(client, source_id)
    if not tracking:
        return None
    
    content_total = tracking["content_total"]
    total_processed = tracking["total_processed"]
    
    if content_total > 0:
        content_progress = total_processed / content_total
        overall_progress = PROGRESS_CONTENT_START + (PROGRESS_CONTENT_END - PROGRESS_CONTENT_START) * content_progress
    else:
        overall_progress = PROGRESS_CONTENT_END
    
    publish_source_progress(
        source_id=source_id,
        status=tracking["status"],
        progress=overall_progress,
        message=f"Processed {total_processed}/{content_total} items",
        source_url=tracking["source_url"],
        source_name=tracking["source_name"],
        content_total=content_total,
        success_content_ids=tracking["success_content_ids"],
        failed_content_ids=tracking["failed_content_ids"],
        has_warning=tracking["has_warning"],
        is_complete=tracking["is_complete"],
    )
    
    return tracking

def _get_content_tracking_state(client: redis.Redis, source_id: str) -> Optional[dict]:
    """
    Get the current content tracking state for a source.
    
    Returns:
        Dict with tracking data, success_ids, failed_ids, and computed fields,
        or None if tracking not found.
    """
    tracking_key = f"source:{source_id}:content_tracking"
    success_key = f"source:{source_id}:success_ids"
    failed_key = f"source:{source_id}:failed_ids"
    
    data = client.get(tracking_key)
    if not data:
        return None
    
    tracking = json.loads(data)
    
    success_ids = list(client.smembers(success_key))
    failed_ids = list(client.smembers(failed_key))
    
    content_total = tracking["content_total"]
    total_processed = len(success_ids) + len(failed_ids)
    
    tracking["success_content_ids"] = success_ids
    tracking["failed_content_ids"] = failed_ids
    tracking["has_warning"] = len(failed_ids) > 0
    tracking["is_complete"] = total_processed == content_total
    tracking["total_processed"] = total_processed

    if tracking["is_complete"]:
        tracking["status"] = SourceStatus.COMPLETED.value
    
    return tracking
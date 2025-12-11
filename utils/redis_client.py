import os
import json
from typing import List, Optional
from contextlib import asynccontextmanager

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
    content_processed: Optional[int] = None,
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
        content_processed: Optional number of content items processed so far
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
    if content_processed is not None:
        event_data["content_processed"] = content_processed
    
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
    counter_key = f"source:{source_id}:content_counter"
    
    tracking_data = {
        "content_total": content_total,
        "source_url": source_url,
        "source_name": source_name,
    }
    
    client.set(tracking_key, json.dumps(tracking_data), ex=BATCH_TTL)
    client.set(counter_key, 0, ex=BATCH_TTL)


def increment_source_content_processed(source_id: str) -> Optional[dict]:
    """
    Increment the content processed count for a source and publish progress.
    Called by content task when it finishes processing.
    
    Uses atomic INCR to safely handle concurrent updates from multiple workers.
    
    Args:
        source_id: UUID of the source
        
    Returns:
        Updated tracking data dict (with content_processed), or None if tracking not found
    """
    client = get_sync_redis_client()
    tracking_key = f"source:{source_id}:content_tracking"
    counter_key = f"source:{source_id}:content_counter"
    
    data = client.get(tracking_key)
    if not data:
        return None
    
    tracking = json.loads(data)
    
    content_processed = client.incr(counter_key)
    client.expire(counter_key, BATCH_TTL)
    
    content_total = tracking["content_total"]
    
    tracking["content_processed"] = content_processed
    
    if content_total > 0:
        content_progress = content_processed / content_total
        overall_progress = PROGRESS_CONTENT_START + (PROGRESS_CONTENT_END - PROGRESS_CONTENT_START) * content_progress
    else:
        overall_progress = PROGRESS_CONTENT_END
    
    publish_source_progress(
        source_id=source_id,
        status="ingesting_content",
        progress=overall_progress,
        message=f"Processed {content_processed}/{content_total} items",
        source_url=tracking["source_url"],
        source_name=tracking["source_name"],
        content_total=content_total,
        content_processed=content_processed,
    )
    
    return tracking
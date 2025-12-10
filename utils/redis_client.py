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
    
    client.publish(channel, json.dumps(event_data))
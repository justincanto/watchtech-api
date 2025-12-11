import asyncio
import json
from source import service, schemas
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import uuid
from sqlalchemy.orm import Session

from db.database import get_db
from auth.service import get_current_user
from auth.schemas import User
from utils.redis_client import get_batch_sources, get_async_redis_client
from db import models

router = APIRouter(
    tags=["source"],
    responses={404: {"description": "Not found"}},
)

@router.get("/progress/{batch_id}")
async def get_source_progress(batch_id: str, db: Session = Depends(get_db)):
    """
    SSE endpoint for tracking source processing progress.
    
    Clients should connect to this endpoint after calling PUT /api/source/
    to receive real-time progress updates for all sources in the batch.
    
    Events:
        - source_progress: Progress update for a specific source
        - batch_complete: All sources in batch are done (completed or failed)
        - error: An error occurred
    """
    # Get the source IDs for this batch
    source_ids = await get_batch_sources(batch_id)
    
    if source_ids is None:
        raise HTTPException(status_code=404, detail="Batch not found or expired")
    
    # If batch exists but has no new sources, return immediately with batch_complete
    if len(source_ids) == 0:
        async def empty_generator():
            yield f"event: batch_complete\ndata: {json.dumps({'batch_id': batch_id, 'message': 'No new sources to process'})}\n\n"
        return StreamingResponse(
            empty_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    
    async def event_generator():
        """Generate SSE events for source progress updates."""
        client = get_async_redis_client()
        pubsub = client.pubsub()
        
        try:
            # Subscribe to progress channels for all sources in the batch
            channels = [f"source:{sid}:progress" for sid in source_ids]
            await pubsub.subscribe(*channels)
            
            # Track completion status for each source
            completed_sources = set()
            
            # First, check current status of all sources and send initial state
            for source_id in source_ids:
                source = db.query(models.Source).filter(
                    models.Source.id == uuid.UUID(source_id)
                ).first()
                
                if source:
                    if source.status == models.SourceStatus.COMPLETED:
                        completed_sources.add(source_id)
                        event_data = {
                            "source_id": source_id,
                            "status": models.SourceStatus.COMPLETED.value,
                            "progress": 1.0,
                            "message": "Source already processed",
                            "source_url": source.url,
                            "source_name": source.name,
                        }
                        yield f"event: source_progress\ndata: {json.dumps(event_data)}\n\n"
                    elif source.status == models.SourceStatus.FAILED:
                        completed_sources.add(source_id)
                        event_data = {
                            "source_id": source_id,
                            "status": models.SourceStatus.FAILED.value,
                            "progress": 0.0,
                            "message": source.error_message or "Processing failed",
                            "source_url": source.url,
                            "source_name": source.name,
                        }
                        yield f"event: source_progress\ndata: {json.dumps(event_data)}\n\n"
                    else:
                        # Source is still processing, send current status
                        event_data = {
                            "source_id": source_id,
                            "status": source.status.value,
                            "progress": 0.0,
                            "message": f"Processing: {source.status.value}",
                            "source_url": source.url,
                            "source_name": source.name,
                        }
                        yield f"event: source_progress\ndata: {json.dumps(event_data)}\n\n"
            
            # Check if all sources are already done
            if len(completed_sources) == len(source_ids):
                yield f"event: batch_complete\ndata: {json.dumps({'batch_id': batch_id, 'message': 'All sources processed'})}\n\n"
                return
            
            # Listen for progress events
            timeout_seconds = 300  # 5 minute timeout
            start_time = asyncio.get_event_loop().time()
            
            async for message in pubsub.listen():
                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    yield f"event: error\ndata: {json.dumps({'message': 'Timeout waiting for progress updates'})}\n\n"
                    break
                
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    source_id = data.get("source_id")
                    
                    # Yield the progress event
                    yield f"event: source_progress\ndata: {json.dumps(data)}\n\n"
                    
                    # Track completed sources
                    if data.get("status") in [models.SourceStatus.COMPLETED.value, models.SourceStatus.FAILED.value]:
                        completed_sources.add(source_id)
                        
                        # Check if all sources are done
                        if len(completed_sources) == len(source_ids):
                            yield f"event: batch_complete\ndata: {json.dumps({'batch_id': batch_id, 'message': 'All sources processed'})}\n\n"
                            break
                            
        except asyncio.CancelledError:
            # Client disconnected
            pass
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await client.aclose()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/{source_id}", response_model=schemas.Source)
def get_source(
    source_id: uuid.UUID, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """Get a specific source by ID with its 12 most recent contents"""
    return service.get_source(db=db, source_id=source_id)

@router.get("/", response_model=schemas.UserSources)
def get_user_sources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all sources for the current user"""
    return {"sources": current_user.sources}


@router.put("/", response_model=schemas.UserSourcesUpdateResponse, status_code=202)
def update_user_sources(
    sources_data: schemas.UserSourcesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the sources for the current user.
    
    Returns immediately with a batch_id that can be used to track progress
    via GET /api/source/progress/{batch_id} (SSE endpoint).
    
    New sources will be processed asynchronously in the background.
    """
    sources_dict = [
        {
            "type": source.type,
            "url": str(source.url)
        }
        for source in sources_data.sources
    ]
    batch_id, sources, new_source_ids = service.update_user_sources(db=db, user_id=current_user.id, sources_data=sources_dict)
    return {"batch_id": batch_id, "sources": sources, "new_source_ids": new_source_ids}

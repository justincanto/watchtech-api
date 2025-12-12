import asyncio
import json
from content import service
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Union
import uuid
from sqlalchemy.orm import Session

from db.database import get_db
from content import schemas
from auth.service import get_current_user
from auth.schemas import User
from db import models
from utils.redis_client import get_async_redis_client, CONTENT_PROCESSED_CHANNEL

router = APIRouter(
    tags=["content"],
    responses={404: {"description": "Not found"}},
)

@router.get("/", response_model=List[schemas.Content])
def get_contents(
    limit: int = 12,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.get_contents(db, current_user.id, limit, offset)


@router.get("/stream")
async def stream_content_events(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    SSE endpoint for streaming content processed events.
    
    Clients can connect to receive real-time notifications when new content
    is processed and ready to be displayed.
    
    Events:
        - content_processed: A new content item is ready
    """
    user_source_ids = set(
        str(sid) for (sid,) in 
        db.query(models.UserSource.source_id)
        .filter(models.UserSource.user_id == current_user.id)
        .all()
    )
    
    async def event_generator():
        client = get_async_redis_client()
        pubsub = client.pubsub()
        
        try:
            await pubsub.subscribe(CONTENT_PROCESSED_CHANNEL)
            
            yield f"event: connected\ndata: {json.dumps({'message': 'Connected to content stream'})}\n\n"
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    source_id = data.get("source_id")
                    
                    if source_id in user_source_ids:
                        yield f"event: content_processed\ndata: {json.dumps(data)}\n\n"
                        
        except asyncio.CancelledError:
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
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/{content_id}", response_model=schemas.Content)
def get_content_by_id(
    content_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    content = service.get_user_content_by_id(db, content_id, current_user.id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    return content

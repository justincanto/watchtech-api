from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
import feedparser

from db.database import get_db
from db import models
from subscriptions.youtube import handle_verification_challenge, process_youtube_webhook
from content import service as content_service


router = APIRouter(tags=["webhooks"])


@router.get("/youtube")
async def youtube_verify(
    request: Request,
    db: Session = Depends(get_db),
):
    params = request.query_params
    mode = params.get("hub.mode")
    topic = params.get("hub.topic")
    challenge = params.get("hub.challenge")
    lease_seconds = params.get("hub.lease_seconds")
    verify_token = params.get("hub.verify_token")
    lease_seconds_int = int(lease_seconds) if lease_seconds else None

    body, status = handle_verification_challenge(
        db=db,
        mode=mode,
        topic=topic,
        challenge=challenge,
        lease_seconds=lease_seconds_int,
        verify_token=verify_token,
    )
    if body is None:
        return Response(status_code=status)
    return PlainTextResponse(content=body, status_code=status)


@router.post("/youtube")
async def youtube_notification(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.body()
    process_youtube_webhook(db, body)

    return Response(status_code=204)

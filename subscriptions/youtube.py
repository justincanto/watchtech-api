import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from sqlalchemy.orm import Session

from source.service import content_service
from db import models


PUBSUB_HUB_URL = os.getenv("YOUTUBE_PUBSUB_HUB", "https://pubsubhubbub.appspot.com/subscribe")
YOUTUBE_FEED_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
YOUTUBE_WEBHOOK_CALLBACK_URL = os.getenv("YOUTUBE_WEBHOOK_CALLBACK_URL", "http://localhost:8000/api/webhooks/youtube")


def _topic_url_for_channel(channel_id: str) -> str:
    return f"{YOUTUBE_FEED_BASE}{channel_id}"

def subscribe_channel(db: Session, source: models.Source) -> models.YouTubeSubscription:
    verify_token = secrets.token_urlsafe(32)

    ytsub: Optional[models.YouTubeSubscription] = (
        db.query(models.YouTubeSubscription)
        .filter(models.YouTubeSubscription.source_id == source.id)
        .first()
    )

    if not ytsub:
        ytsub = models.YouTubeSubscription(
            source_id=source.id,
            channel_id=source.original_id,
            verify_token=verify_token,
            verified=False,
        )
        db.add(ytsub)
    else:
        ytsub.verify_token = verify_token
        ytsub.verified = False

    db.commit()
    db.refresh(ytsub)

    # Request subscription with default 7-day lease (max allowed by hub is typically ~ 7 days)
    params = {
        "hub.mode": "subscribe",
        "hub.topic": _topic_url_for_channel(source.original_id),
        "hub.callback": YOUTUBE_WEBHOOK_CALLBACK_URL,
        "hub.lease_seconds": str(6 * 24 * 3600),  # 6 days; we'll renew daily
        "hub.verify": "async",
        "hub.verify_token": verify_token,
    }

    response = requests.post(PUBSUB_HUB_URL, data=params, timeout=10)
    response.raise_for_status()

    return ytsub


def unsubscribe_channel(db: Session, source: models.Source) -> None:
    params = {
        "hub.mode": "unsubscribe",
        "hub.topic": _topic_url_for_channel(source.original_id),
        "hub.callback": YOUTUBE_WEBHOOK_CALLBACK_URL,
        "hub.verify": "async",
    }
    
    try:
        requests.post(PUBSUB_HUB_URL, data=params, timeout=10)
    finally:
        db.query(models.YouTubeSubscription).filter(models.YouTubeSubscription.source_id == source.id).delete()
        db.commit()


def renew_due_subscriptions(db: Session) -> int:
    due = (
        db.query(models.YouTubeSubscription)
        .filter(
            (models.YouTubeSubscription.lease_expires_at == None)
            | (models.YouTubeSubscription.lease_expires_at <= datetime.now(timezone.utc) + timedelta(days=2))
        )
        .all()
    )

    renewed = 0
    for sub in due:
        source = db.query(models.Source).filter(models.Source.id == sub.source_id).first()
        if source is None:
            continue
        subscribe_channel(db, source)
        renewed += 1
    return renewed


def handle_verification_challenge(
    db: Session,
    mode: str,
    topic: str,
    challenge: str,
    lease_seconds: Optional[int],
    verify_token: Optional[str],
):
    channel_id = topic.split("channel_id=")[-1]
    youtube_subscription = (
        db.query(models.YouTubeSubscription)
        .filter(models.YouTubeSubscription.channel_id == channel_id)
        .first()
    )
    if not youtube_subscription:
        return None, 404

    if verify_token and youtube_subscription.verify_token and verify_token != youtube_subscription.verify_token:
        return None, 403

    if mode == "subscribe":
        youtube_subscription.verified = True
        if lease_seconds:
            youtube_subscription.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        db.commit()
        return challenge, 200
    elif mode == "unsubscribe":
        db.delete(youtube_subscription)
        db.commit()
        return challenge, 200
    else:
        return None, 400


def process_youtube_webhook(db: Session, body: bytes) -> None:
    """
    Process YouTube PubSubHubbub webhook notification.
    
    New video notifications are queued for background processing via Celery
    for better scalability and reliability.
    """
    parsed = feedparser.parse(body)
    for entry in parsed.entries:
        video_id = getattr(entry, "yt_videoid", None)
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        channel_id = getattr(entry, "yt_channelid", None)

        source = (
            db.query(models.Source)
            .filter(models.Source.type == models.SourceType.YOUTUBE)
            .filter(models.Source.original_id == channel_id)
            .first()
        )
        if not source:
            continue

        try:
            content_service.retrieve_content_for_source(
                db, source, video_url
            )
        except Exception as e:
            print(f"Error queueing content {video_url} for source {source.id}: {e}")
            continue
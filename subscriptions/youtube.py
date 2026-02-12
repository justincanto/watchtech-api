import logging

from db.database import SessionLocal
from sqlalchemy.orm import Session

from content import service as content_service
from db import models
from extractors.youtube import get_youtube_channel_videos

logger = logging.getLogger(__name__)

def poll_youtube_job():
    print("Polling YouTube channels")
    db: Session = SessionLocal()
    try:
        queued_count = poll_youtube_channels(db)
        if queued_count:
            print(f"Polled YouTube channels — queued {queued_count} new videos")
    finally:
        db.close()


def poll_youtube_channels(db: Session) -> int:
    """
    Poll all completed YouTube sources for new videos.

    For each source, fetches recent video URLs via yt-dlp and queues any
    new ones for processing.  Deduplication is handled by
    content_service.queue_content_processing.

    Returns the number of new videos queued.
    """
    sources = (
        db.query(models.Source)
        .filter(
            models.Source.type == models.SourceType.YOUTUBE,
            models.Source.status == models.SourceStatus.COMPLETED,
            models.Source.url.isnot(None),
        )
        .all()
    )

    queued_count = 0
    for source in sources:
        try:
            video_urls = get_youtube_channel_videos(source.url)
            for url in video_urls:
                try:
                    content_service.queue_content_processing(db, source, url)
                    queued_count += 1
                except Exception as e:
                    logger.error(
                        f"Error queueing video {url} for source {source.id}: {e}"
                    )
        except Exception as e:
            logger.error(f"Error polling YouTube channel {source.url}: {e}")

    return queued_count

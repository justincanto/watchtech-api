from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from typing import List, Optional
from urllib.parse import urlparse, parse_qs

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
except Exception:  # pragma: no cover
    YouTubeTranscriptApi = None
    TranscriptsDisabled = NoTranscriptFound = VideoUnavailable = Exception


router = APIRouter()


class TranscriptSegment(BaseModel):
    text: str
    start: float
    duration: float


class TranscriptResponse(BaseModel):
    video_id: str
    language: Optional[str]
    segments: List[TranscriptSegment]
    formatted_text: str

def extract_video_id(video_url: str) -> Optional[str]:
    parsed = urlparse(video_url)
    if parsed.hostname in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query)
        video_ids = query.get("v")
        if video_ids:
            return video_ids[0]
        # youtu.be redirect paths sometimes appear under youtube.com/shorts/<id>
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[-1].split("?")[0]
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        return parsed.path.lstrip("/").split("?")[0]
    return None


@router.get("/", response_model=TranscriptResponse)
async def get_transcript(url: HttpUrl = Query(..., description="YouTube video URL")):
    if YouTubeTranscriptApi is None:
        raise HTTPException(status_code=500, detail="youtube-transcript-api not available")

    video_id = extract_video_id(str(url))
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL; could not extract video id")

    try:
        # Prefer a non-generated transcript (likely the video's original language),
        # fall back to any available transcript.
        transcript_list = YouTubeTranscriptApi().list(video_id)
        transcript = transcript_list.find_transcript(['en', 'fr'])
        # for t in transcript_list:  # iteration preserves library's priority ordering
        #     if not getattr(t, "is_generated", False):
        #         transcript = t
        #         break
        # if transcript is None:
        #     transcript = next(iter(transcript_list), None)
        if transcript is None:
            raise NoTranscriptFound(video_id)

        raw_segments = transcript.fetch()
        segments = [
            TranscriptSegment(
                text=str(getattr(seg, "text", "")),
                start=float(getattr(seg, "start", 0.0)),
                duration=float(getattr(seg, "duration", 0.0)),
            )
            for seg in raw_segments
        ]
        language = getattr(transcript, "language", None)
        # Build a clean, human-readable transcript
        # - remove bracketed cues like [Music], [Applause]
        # - collapse excessive whitespace
        # - join sentences with spaces
        import re

        def clean_text(s: str) -> str:
            s = re.sub(r"\[[^\]]*\]", "", s)  # remove [bracketed]
            s = s.replace("\n", " ")
            s = re.sub(r"\s+", " ", s).strip()
            return s

        formatted_text = clean_text(" ".join([seg.text for seg in segments if seg.text]))

        return TranscriptResponse(video_id=video_id, language=language, segments=segments, formatted_text=formatted_text)
    except TranscriptsDisabled:
        raise HTTPException(status_code=403, detail="Transcripts are disabled for this video")
    except NoTranscriptFound:
        raise HTTPException(status_code=404, detail="No transcript found for this video")
    except VideoUnavailable:
        raise HTTPException(status_code=404, detail="Video is unavailable")
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to retrieve transcript: {e}")



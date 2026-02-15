import sys
import json
import requests
import feedparser
from yt_dlp import YoutubeDL
from datetime import datetime
from typing import List
import os


class NonVideoContentError(Exception):
    """Raised when content is not a video (e.g. shorts, live streams)."""
    pass

RESIDENTIAL_PROXY = os.getenv("RESIDENTIAL_PROXY")
if not RESIDENTIAL_PROXY:
    raise Exception("RESIDENTIAL_PROXY is not set")

def get_proxy():
    return {
        "http": RESIDENTIAL_PROXY,
        "https": RESIDENTIAL_PROXY
    }

ENGLISH_LANGUAGE_CODE = "en"

def scrap_video(url):
    """
    Retrieves subtitles (transcript) for a given YouTube video URL.
    It first attempts to get the manually provided subtitles; if not available,
    it falls back to auto-generated captions.
    """
    # Configure yt-dlp options.
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'quiet': True,
        'no_warnings': True,
        'subtitlesformat': 'json3'
    }

    ydl_opts["proxy"] = RESIDENTIAL_PROXY
    
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info['title']
    publisher = info['uploader']
    publisher_id = info['channel_id']
    description = info['description']
    uploader_url = info['uploader_url']
    video_language = info.get('language', ENGLISH_LANGUAGE_CODE)
    published_at = datetime.fromtimestamp(info['timestamp'])
    media_type = info['media_type']

    if media_type != 'video':
        raise NonVideoContentError("Media type is not video but: " + media_type)

    caption_url = get_caption_url(video_language, info)
    if not caption_url and video_language != ENGLISH_LANGUAGE_CODE:
        caption_url = get_caption_url(ENGLISH_LANGUAGE_CODE, info)

    if not caption_url:
        raise Exception("No subtitles or automatic captions available for this video.")

    response = requests.get(caption_url, proxies=get_proxy())
    if response.status_code != 200:
        raise Exception("Failed to download the subtitles.")

    transcript = format_transcript(response.text)

    return {
        "content": transcript, 
        "title": title, 
        "publisher": publisher, 
        "publisher_id": publisher_id, 
        "publisher_url": uploader_url,
        "description": description, 
        "published_at": published_at
    }

def get_caption_url(lang_code, info):
    subtitles = info.get('subtitles') or {}
    caption_data = subtitles.get(lang_code)
    
    if not caption_data:
        auto_captions = info.get('automatic_captions') or {}
        caption_data = None
        for k, v in auto_captions.items():
            if k.endswith('-orig'):
                caption_data = v
                break
        
    if caption_data:
        for caption in caption_data:
            if caption.get('ext') == 'json3':
                return caption.get('url')
            
        raise Exception("No json3 format subtitles available")
    return None

def format_transcript(json_text):
    """Parses yt-dlp 'json3'-formatted subtitles into plain transcript text.    """
    data = json.loads(json_text)
    clean_transcript = ""
    for event in data.get("events", []):
        segs = event.get("segs", [])
        for seg in segs:
            if seg.get("utf8"):
                clean_transcript += seg.get("utf8")
                
    return clean_transcript

def get_channel_data(channel_url):
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        "extract_flat": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    publisher = info['uploader']
    publisher_id = info['channel_id']
    return {"name": publisher, "id": publisher_id}

def get_youtube_channel_videos(channel_url: str, limit: int = 5) -> List[str]:
    """
    Get the URLs of the most recent videos from a YouTube channel
    
    Args:
        channel_url (str): URL of the YouTube channel
        limit (int): Maximum number of videos to retrieve
        
    Returns:
        List[str]: List of video URLs
    """
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        "extract_flat": True,
    }
    ydl_opts["proxy"] = RESIDENTIAL_PROXY

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)

        entries = info.get("entries") or []
        if not entries:
            return []

        video_entries = entries[0].get("entries") if entries[0]["_type"] == "playlist" else entries
        video_urls = [entry.get("url") for entry in video_entries[:limit]]

        return video_urls
    except Exception as e:
        print(f"Error retrieving YouTube videos: {e}")
        return []


def get_youtube_channel_feed_videos(channel_id: str) -> List[str]:
    """
    Get recent video URLs from a YouTube channel's public XML feed.

    Uses the free Atom feed (no proxy or API key needed) and filters out
    Shorts by checking each entry's link for '/shorts/'.

    Args:
        channel_id: The YouTube channel ID (e.g. UC...)

    Returns:
        List of video URLs (https://www.youtube.com/watch?v=...)
    """
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)

    video_urls = []
    for entry in feed.entries:
        # Each entry has a list of links; check if any point to /shorts/
        is_short = any("/shorts/" in link.get("href", "") for link in entry.get("links", []))
        if is_short:
            continue

        video_id = entry.get("yt_videoid")
        if video_id:
            video_urls.append(f"https://www.youtube.com/watch?v={video_id}")

    return video_urls[:5]

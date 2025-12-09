import sys
import json
import requests
from yt_dlp import YoutubeDL
from datetime import datetime
import feedparser
from typing import List
import os

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

    def get_caption_url(lang_code):
        # Try manual subtitles first
        subtitles = info.get('subtitles') or {}
        caption_data = subtitles.get(lang_code)
        
        # If manual subtitles aren't available, look for auto-generated captions
        if not caption_data:
            auto_captions = info.get('automatic_captions') or {}
            caption_data = auto_captions.get(lang_code)
            
        if caption_data:
            for caption in caption_data:
                if caption.get('ext') == 'json3':
                    return caption.get('url')
                
            raise Exception("No json3 format subtitles available")
        return None

    # Try video's language first, then fallback to English
    caption_url = get_caption_url(video_language)
    if not caption_url and video_language != ENGLISH_LANGUAGE_CODE:
        caption_url = get_caption_url(ENGLISH_LANGUAGE_CODE)

    if not caption_url:
        raise Exception("No subtitles or automatic captions available for this video.")

    response = requests.get(caption_url, proxies=get_proxy())
    if response.status_code != 200:
        raise Exception("Failed to download the subtitles.")

    transcript = format_transcript(response.text)
    print({
        "content": transcript, 
        "title": title, 
        "publisher": publisher, 
        "publisher_id": publisher_id, 
        "publisher_url": uploader_url,
        "description": description, 
        "published_at": published_at
    })
    return {
        "content": transcript, 
        "title": title, 
        "publisher": publisher, 
        "publisher_id": publisher_id, 
        "publisher_url": uploader_url,
        "description": description, 
        "published_at": published_at
    }

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
    # Convert channel URL to channel ID if needed
    channel_data = get_channel_data(channel_url)
    
    # We'll use the YouTube RSS feed to get recent videos
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_data['id']}"
    try:
        response = requests.get(feed_url, proxies=get_proxy())
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        # Extract video URLs from the feed
        video_urls = []
        count = 0
        while count < limit and count < len(feed.entries):
            entry = feed.entries[count]
            link_href = ""
            if hasattr(entry, "links"):
                for l in entry.links:
                    if l.get("rel") == "alternate" and l.get("href"):
                        link_href = l.get("href")
                        break
            if "youtube.com/shorts/" in link_href:
                continue

            video_urls.append(link_href)
            count += 1

        return video_urls
    except Exception as e:
        print(f"Error retrieving YouTube videos: {e}")
        return []

def main():
    if len(sys.argv) != 2:
        print("Usage: python youtube_extractor.py <YouTube Video URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    try:
        channel_data = scrap_video(url)
        print("Channel data:\n")
        # print(channel_data.get("content"))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

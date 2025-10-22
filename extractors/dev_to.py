import sys
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import re
from typing import List

def extract_dev_to_publisher_id(url):
    """
    Extract dev.to username from URL
    
    Args:
        url (str): dev.to profile or article URL
        
    Returns:
        str: dev.to username
    """
    # Handle different URL formats
    patterns = [
        r'dev\.to/([^/]+)',     # dev.to/username or dev.to/username/article
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            # If it's an article URL, the username part comes before the article slug
            if '/' in username:
                username = username.split('/')[0]
            return username
    
    raise ValueError(f"Could not extract username from dev.to URL: {url}")

def scrap_article(url):
    """
    Get content from a specific dev.to article URL
    
    Args:
        url (str): dev.to article URL
        
    Returns:
        dict: Article data including title, content, publisher, publisher id and publication date
    """
    
    # Extract article data using the dev.to RSS feed
    # Find the author in the URL
    try:
        publisher_id = extract_dev_to_publisher_id(url)
        feed_url = f"https://dev.to/feed/{publisher_id}"
        
        feed_response = requests.get(feed_url)
        if feed_response.status_code != 200:
            raise Exception(f"Failed to retrieve dev.to feed: {feed_response.status_code}")
        
        feed = feedparser.parse(feed_response.content)

        publisher = feed.feed.title.lstrip("DEV Community: ")        
        
        # Extract article slug from URL
        article_slug = url.split('/')[-1]
        
        # Find the matching article
        for entry in feed.entries:
            if article_slug in entry.link:
                content = entry.content[0].value if 'content' in entry else entry.summary
                soup = BeautifulSoup(content, 'html.parser')
                clean_content = soup.get_text(separator='\n').strip()
                
                return {
                    "title": entry.title,
                    "content": clean_content,
                    "publisher_id": publisher_id,
                    "publisher": publisher,
                    "published_at": datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z"),
                    "url": url,
                    "publisher_url": f"https://dev.to/{publisher_id}"
                }
    except Exception as e:
        print(f"Could not find article in feed: {e}. Falling back to direct scraping.")
    
    # For direct article URLs, we need to fetch the page and extract content
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to retrieve dev.to article: {response.status_code}")
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Extract article info
    title = soup.find('h1').text.strip()
    
    # Get author details
    publisher_id = extract_dev_to_publisher_id(url)
    author_element = soup.select_one('a[href^="/'+publisher_id+'"]:not(:empty):not(:has(img))')
    publisher = author_element.text.strip()
    
    # Find publication date
    time_element = soup.find('time')
    if time_element and time_element.get('datetime'):
        published_at = datetime.fromisoformat(time_element['datetime'].replace('Z', '+00:00'))
    else:
        published_at = datetime.now()
    
    # Extract article content
    article_content = soup.select_one('article .crayons-article__body')
    if article_content:
        # Remove unnecessary elements
        for element in article_content.select('.highlight, .admin-action-comment-marker'):
            element.decompose()
        clean_content = article_content.get_text(separator='\n').strip()
    else:
        clean_content = "Could not extract article content"
    
    return {
        "title": title,
        "content": clean_content,
        "publisher_id": publisher_id,
        "publisher": publisher,
        "published_at": published_at,
        "url": url,
        "publisher_url": f"https://dev.to/{publisher_id}"
    }

def get_author_data(url: str):
    """
    Get author data from a dev.to profile URL
    
    Args:
        url (str): dev.to profile URL
        
    Returns:
        dict: Author data including name and id
    """
    try:
        publisher_id = extract_dev_to_publisher_id(url)
    except ValueError as e:
        raise ValueError(f"Invalid dev.to profile URL: {e}")
    
    # Try to get data from feed
    feed_url = f"https://dev.to/feed/{publisher_id}"
    feed_response = requests.get(feed_url)
    if feed_response.status_code != 200:
        # If can't access feed, try to get from profile page
        profile_url = f"https://dev.to/{publisher_id}"
        profile_response = requests.get(profile_url)
        if profile_response.status_code != 200:
            raise ValueError(f"Could not access dev.to profile for user {publisher_id}. Status code: {profile_response.status_code}")
    
    feed = feedparser.parse(feed_response.content)
    
    if len(feed.entries) == 0:
        raise ValueError(f"No articles found for user {publisher_id}")
    
    return {
        "name": feed.feed.title.lstrip("DEV Community: "),
        "id": publisher_id
    }

def get_dev_to_author_articles(author_url: str, limit: int = 5) -> List[str]:
    """
    Get the URLs of the most recent articles from a dev.to author
    
    Args:
        author_url (str): URL of the dev.to author
        limit (int): Maximum number of articles to retrieve
        
    Returns:
        List[str]: List of article URLs
    """
    try:
        username = extract_dev_to_publisher_id(author_url)
        feed_url = f"https://dev.to/feed/{username}"
        
        response = requests.get(feed_url)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        # Extract article URLs from the feed
        article_urls = []
        for entry in feed.entries[:limit]:
            article_urls.append(entry.link)
            
        return article_urls
    except Exception as e:
        print(f"Error retrieving dev.to articles: {e}")
        return [] 

def main():
    if len(sys.argv) != 2:
        print("Usage: python dev_to.py <dev.to URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    try:
        data = get_author_data(url)
        print(f"Author data: {data}")
        # print("Article data:")
        # print(f"Title: {data['title']}")
        # print(f"Publisher: {data['publisher']} (@{data['publisher_id']})")
        # print(f"Publication Date: {data['published_at']}")
        # print(f"Length of content: {len(data['content'])} characters")
        # print(f"content: {data['content']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main() 
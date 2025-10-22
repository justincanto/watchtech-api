import sys
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import re
from typing import List

def extract_medium_username(url):
    """
    Extract Medium username from URL
    
    Args:
        url (str): Medium profile URL
        
    Returns:
        str: Medium username
    """
    # Handle different URL formats
    patterns = [
        r'medium\.com/@([^/]+)',     # medium.com/@username
        r'medium\.com/([^/]+)',      # medium.com/username
        r'@([^/]+)\.medium\.com',    # @username.medium.com
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    raise ValueError(f"Could not extract username from Medium URL: {url}")

def scrap_article(url):
    """
    Get content from a specific Medium article URL
    
    Args:
        url (str): Medium article URL
        
    Returns:
        dict: Article data including title, content, description, author, author id and publication date
    """
    
    # Extract article data using the medium RSS feed
    # Find the author in the URL
    username = extract_medium_username(url)
    feed_url = f"https://medium.com/feed/@{username}"
    
    feed_response = requests.get(feed_url)
    if feed_response.status_code != 200:
        raise Exception(f"Failed to retrieve Medium feed: {feed_response.status_code}")
    
    feed = feedparser.parse(feed_response.content)
    
    # Find the matching article
    for entry in feed.entries:
        post_id = entry.id.split('https://medium.com/p/')[1]
        if post_id in url:
            print(f"Found article:")
            if not entry.content:
                raise Exception(f"No content found in article: {entry.title}")
            
            content = entry.content[0].value
            soup = BeautifulSoup(content, 'html.parser')
            clean_content = soup.get_text(separator='\n').strip()
            

            return {
                "title": entry.title,
                "content": clean_content,
                "publisher_id": username,
                "publisher": entry.author,
                "published_at": datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %Z"),
                "url": url,
                "publisher_url": f"https://medium.com/@{username}"
            }
    
    # For direct article URLs, we need to fetch the page and extract content
    response = requests.get(url)
    # If we couldn't find the article in the feed, try extracting from HTML
    soup = BeautifulSoup(response.text, 'html.parser')

    published_at = soup.find("meta", property="article:published_time")["content"]
    author_name = soup.find("meta", {"name":"author"})["content"]

    h1_tag = soup.find('h1')
    title = h1_tag.text.strip()

    div_next_to_h1 = h1_tag.find_next_sibling('div')
    if div_next_to_h1:
        # Remove the div from the article content
        div_next_to_h1.decompose()
    
    # Try to extract the article content
    article_content = soup.find('article')
    if article_content:
        paragraphs = article_content.find_all('p')
        clean_content = '\n'.join([p.text for p in paragraphs])
    else:
        clean_content = "Could not extract article content"
    
    return {
        "title": title,
        "content": clean_content,
        "publisher_id": username,
        "publisher": author_name,
        "published_at": datetime.strptime(published_at.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ"),
        "url": url,
        "publisher_url": f"https://medium.com/@{username}"
    }

def get_author_data(url: str):
    """
    Get author data from a Medium profile URL
    
    Args:
        url (str): Medium profile URL
        
    Returns:
        dict: Author data including name and id
    """

    try:
        username = extract_medium_username(url)
    except ValueError as e:
        raise ValueError(f"Invalid Medium profile URL: {e}")
        
    # Validate the feed URL can be accessed
    feed_url = f"https://medium.com/feed/@{username}"
    feed_response = requests.get(feed_url)
    if feed_response.status_code != 200:
        raise ValueError(f"Could not access Medium feed for user {username}. Status code: {feed_response.status_code}")

    feed = feedparser.parse(feed_response.content)

    if len(feed.entries) == 0:
        raise ValueError(f"No articles found for user {username}")

    return {
        "name": feed.entries[0].author,
        "id": username
    }

def get_medium_author_articles(author_url: str, limit: int = 5) -> List[str]:
    """
    Get the URLs of the most recent articles from a Medium author
    
    Args:
        author_url (str): URL of the Medium author
        limit (int): Maximum number of articles to retrieve
        
    Returns:
        List[str]: List of article URLs
    """
    try:
        username = extract_medium_username(author_url)
        feed_url = f"https://medium.com/feed/@{username}"
        
        response = requests.get(feed_url)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        # Extract article URLs from the feed
        article_urls = []
        for entry in feed.entries[:limit]:
            article_urls.append(entry.link)
            
        return article_urls
    except Exception as e:
        print(f"Error retrieving Medium articles: {e}")
        return []

def main():
    if len(sys.argv) != 2:
        print("Usage: python medium.py <Medium URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    try:
        data = get_author_data(url)
        print("Author data:")
        print(f"Name: {data['name']}")
        print(f"ID: {data['id']}")
        # data = scrap_article(url)
        # print("Article data:")
        # print(f"Title: {data['title']}")
        # print(f"Author: {data['author']} (@{data['author_id']})")
        # print(f"Description: {data['description']}")
        # print(f"Publication Date: {data['published_at']}")
        # print(f"Length of content: {len(data['content'])} characters")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
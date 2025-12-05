from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status, Request, Response, Cookie
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
from dotenv import load_dotenv
import uuid
import secrets

from db.database import get_db
from db import models
from auth import schemas
from authlib.integrations.httpx_client import AsyncOAuth2Client

# Load environment variables
load_dotenv()

# Cookie settings
SESSION_COOKIE_KEY = "session_token"
SESSION_DURATION_MINUTES = 20160  # 2 weeks in minutes

# Google OAuth settings
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

def create_user_session(db: Session, user_id: uuid.UUID, user_agent: Optional[str] = None, token: Optional[str] = None, google_access_token: Optional[str] = None, google_token_expires_at: Optional[datetime] = None) -> models.UserSession:
    """Create a new user session in the database"""
    # Generate a secure token
    if not token:
        token = secrets.token_urlsafe(32)
    
    # Calculate expiration date (2 weeks from now)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_DURATION_MINUTES)
    
    # Create session
    db_session = models.UserSession(
        user_id=user_id,
        token=token,
        expires_at=expires_at,
        user_agent=user_agent,
        google_access_token=google_access_token,
        google_token_expires_at=google_token_expires_at
    )
    
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    
    return db_session

def get_session_by_token(db: Session, token: str) -> Optional[models.UserSession]:
    """Get a session by token"""
    return db.query(models.UserSession).filter(
        models.UserSession.token == token,
        models.UserSession.expires_at > datetime.now(timezone.utc)
    ).first()

def extend_session(db: Session, session: models.UserSession) -> models.UserSession:
    """Extend a session's expiration time by 2 more weeks"""
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_DURATION_MINUTES)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session

def logout(db: Session, session: models.UserSession, response: Response) -> None:
    """Delete session and session cookie"""
    db.delete(session)
    db.commit()

    response.delete_cookie(key=SESSION_COOKIE_KEY)

async def get_current_user(
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_KEY)
) -> models.User:
    """Get the current user from session cookie"""
    error_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Cookie"},
    )
    if not session_token:
        raise error_exception
    
    # Get session from database
    session = get_session_by_token(db, session_token)
    if not session:
        raise error_exception
    
    # Get user from session
    user = get_user_by_id(db, session.user_id)
    if not user:
        raise error_exception
    
    # Check if user is subscribed
    if not user.is_subscribed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription required",
        )
    
    # Extend session expiration
    extend_session(db, session)

    # Check/Refresh Google Access Token
    if session.google_token_expires_at and session.google_token_expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
        new_token_info = await refresh_google_token(user)
        if new_token_info:
            session.google_access_token = new_token_info.get("access_token")
            expires_in = new_token_info.get("expires_in", 3600)
            session.google_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            db.commit()
            db.refresh(session)
    
    user.session = session
    return user

def get_user_by_email(db: Session, email: str):
    """Get user by email"""
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_id(db: Session, user_id: uuid.UUID):
    """Get user by id"""
    return db.query(models.User).filter(models.User.id == user_id).first()


def create_user(db: Session, user_data: dict, refresh_token: Optional[str] = None):
    """Create a new user from Google profile data"""
    db_user = models.User(
        email=user_data['email'],
        name=user_data['name'],
        picture=user_data['picture'],
        google_id=user_data['sub'],
        google_refresh_token=refresh_token
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_google_auth_url():
    """Generate Google OAuth authentication URL"""
    return f"{GOOGLE_AUTH_URL}?client_id={GOOGLE_CLIENT_ID}&redirect_uri={os.getenv('GOOGLE_REDIRECT_URI')}&scope=openid%20email%20profile&response_type=code&access_type=offline&prompt=consent"

async def google_auth_callback(code: str, db: Session, response: Response, user_agent: Optional[str]):
    """Handle Google OAuth callback"""
    client = AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scope="openid email profile",
        redirect_uri=GOOGLE_REDIRECT_URI,
        token_endpoint=GOOGLE_TOKEN_URL,
        code_challenge_method="S256",
    )
    
    token = await client.fetch_token(
        GOOGLE_TOKEN_URL,
        code=code
        )
    
    client.token = token
    user_info = await client.get(GOOGLE_USERINFO_URL)
    user_info = user_info.json()
    
    refresh_token = token.get("refresh_token")
    access_token = token.get("access_token")
    expires_in = token.get("expires_in", 3600)
    google_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    email = user_info.get("email")
    user = get_user_by_email(db, email)
    if not user:
        user = create_user(db, user_info, refresh_token)
    elif refresh_token:
        # Update refresh token if provided
        user.google_refresh_token = refresh_token
        db.commit()
        db.refresh(user)
    
    session = create_user_session(
        db=db,
        user_id=user.id, 
        user_agent=user_agent,
        google_access_token=access_token,
        google_token_expires_at=google_token_expires_at
    )

    response.set_cookie(
        key=SESSION_COOKIE_KEY,
        value=session.token,
        expires=session.expires_at.astimezone(timezone.utc),
        domain = "localhost" if os.getenv("ENVIRONMENT") == "development" else os.getenv("DOMAIN_NAME", None),
        httponly=True,
        secure=os.getenv("ENVIRONMENT") != "development",
        samesite="lax"
    )

async def refresh_google_token(user: models.User) -> Optional[dict]:
    """Refresh Google access token using refresh token"""
    if not user.google_refresh_token:
        return None
        
    client = AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_endpoint=GOOGLE_TOKEN_URL,
        grant_type='refresh_token',
        refresh_token=user.google_refresh_token
    )
    
    try:
        new_token = await client.refresh_token(GOOGLE_TOKEN_URL)
        return new_token
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None

        
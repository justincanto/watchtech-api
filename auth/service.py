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

def create_user_session(db: Session, user_id: uuid.UUID, user_agent: Optional[str] = None, token: Optional[str] = None) -> models.UserSession:
    """Create a new user session in the database"""
    # Generate a secure token
    if not token:
        token = secrets.token_urlsafe(32)
    
    # Calculate expiration date (2 weeks from now)
    expires_at = datetime.utcnow() + timedelta(minutes=SESSION_DURATION_MINUTES)
    
    # Create session
    db_session = models.UserSession(
        user_id=user_id,
        token=token,
        expires_at=expires_at,
        user_agent=user_agent,
    )
    
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    
    return db_session

def get_session_by_token(db: Session, token: str) -> Optional[models.UserSession]:
    """Get a session by token"""
    return db.query(models.UserSession).filter(
        models.UserSession.token == token,
        models.UserSession.expires_at > datetime.utcnow()
    ).first()

def extend_session(db: Session, session: models.UserSession) -> models.UserSession:
    """Extend a session's expiration time by 2 more weeks"""
    session.expires_at = datetime.utcnow() + timedelta(minutes=SESSION_DURATION_MINUTES)
    session.updated_at = datetime.utcnow()
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
    
    return user

async def get_current_user_session(
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_KEY)
):
    """Get both the current user and session"""
    unauthenticated_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Cookie"},
    )
    if not session_token:
        raise unauthenticated_exception
    
    # Get session from database
    session = get_session_by_token(db, session_token)
    if not session:
        raise unauthenticated_exception
    
    # Get user from session
    user = get_user_by_id(db, session.user_id)
    if not user:
        raise unauthenticated_exception
    
    # Extend session expiration
    extend_session(db, session)
    
    return user, session

def get_user_by_email(db: Session, email: str):
    """Get user by email"""
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_id(db: Session, user_id: uuid.UUID):
    """Get user by id"""
    return db.query(models.User).filter(models.User.id == user_id).first()


def create_user(db: Session, user_data: schemas.UserCreate):
    """Create a new user from Google profile data"""
    db_user = models.User(
        email=user_data['email'],
        name=user_data['name'],
        picture=user_data['picture'],
        google_id=user_data['sub']
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_google_auth_url():
    """Generate Google OAuth authentication URL"""
    return f"{GOOGLE_AUTH_URL}?client_id={GOOGLE_CLIENT_ID}&redirect_uri={os.getenv('GOOGLE_REDIRECT_URI')}&scope=openid%20email%20profile&response_type=code"

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

    email = user_info.get("email")
    user = get_user_by_email(db, email)
    if not user:
        user = create_user(db, user_info)
    
    session = create_user_session(
        db=db,
        user_id=user.id, 
        user_agent=user_agent,
        token=token["access_token"]
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
        
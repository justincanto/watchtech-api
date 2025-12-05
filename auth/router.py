from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from auth import service, schemas
from db.database import get_db
import os

router = APIRouter(
    tags=["auth"],
    responses={401: {"description": "Unauthorized"}},
)

@router.get("/login")
async def login():
    """Login with Google SSO, redirects to Google authentication page"""
    return RedirectResponse(service.get_google_auth_url())

@router.get("/callback")
async def callback(request: Request, response: Response, code: str, db: Session = Depends(get_db)):
    """Handle OAuth2 callback from Google"""
    response = RedirectResponse(url=f"{os.getenv('FRONTEND_URL')}/app")
    await service.google_auth_callback(code, db, response, request.headers.get("user-agent"))
    return response

@router.get("/me", response_model=schemas.User)
async def get_current_user(
    current_user: schemas.User = Depends(service.get_current_user)
):
    """Get current authenticated user data"""
    # Get user sources and ingest new content in parallel    
    return current_user

@router.post("/logout")
async def logout(
    response: Response,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(service.get_current_user)
):
    """Logout the current user"""
    service.logout(db, current_user.session, response)
    
    return {"detail": "Successfully logged out"}

@router.get("/session-status")
async def session_status(
    user: schemas.User = Depends(service.get_current_user)
):
    """Check if the current session is valid"""
    return {"status": "authenticated", "user_id": str(user.id)} 
from celery_app import celery_app
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import uvicorn
from content.router import router as content_router
from source.router import router as source_router
from auth.router import router as auth_router
from db.database import Base, engine
from subscriptions.router import router as subscriptions_router
from transcript.router import router as transcript_router
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from db.database import SessionLocal
from subscriptions.youtube import renew_due_subscriptions
import os
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from contextlib import asynccontextmanager

# Create database tables
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()

    def _renew_subscriptions_job():
        db: Session = SessionLocal()
        try:
            renewed = renew_due_subscriptions(db)
            if renewed:
                print(f"Renewed {renewed} YouTube subscriptions")
        finally:
            db.close()

    # Run daily at 03:00 UTC
    scheduler.add_job(_renew_subscriptions_job, "cron", hour=3, minute=0)
    scheduler.start()

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="WatchTech API",
    version="1.0.0",
    lifespan=lifespan,
)

# Create the FastAPI app
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "https://watchtech.io")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(content_router, prefix="/api/content", tags=["content"])
app.include_router(source_router, prefix="/api/source", tags=["source"])
app.include_router(subscriptions_router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(transcript_router, prefix="/api/transcript", tags=["transcript"])

@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True) 

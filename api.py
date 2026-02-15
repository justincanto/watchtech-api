from celery_app import celery_app
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from content.router import router as content_router
from source.router import router as source_router
from auth.router import router as auth_router
from db.database import Base, engine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from subscriptions.youtube import poll_youtube_job
import os
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from contextlib import asynccontextmanager

# Create database tables
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()

    # Poll YouTube channels every 15 minutes
    scheduler.add_job(poll_youtube_job, "interval", hours=2)
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


@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True) 

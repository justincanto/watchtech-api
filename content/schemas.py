import uuid
from typing import Optional
from pydantic import BaseModel, HttpUrl
from datetime import datetime
from db.models import SourceType, ContentStatus

class SourceBase(BaseModel):
    type: SourceType
    url: HttpUrl
    original_id: str
    name: str

class Source(SourceBase):
    id: uuid.UUID
    
    class Config:
        from_attributes = True

class Content(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str] = None
    url: HttpUrl
    summary: Optional[str] = None  # May be None while processing
    source: Source
    published_at: Optional[datetime] = None  # May be None while processing
    created_at: datetime
    status: ContentStatus = ContentStatus.COMPLETED
    error_message: Optional[str] = None
    task_id: Optional[str] = None

    class Config:
        from_attributes = True
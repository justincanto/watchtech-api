import uuid
from pydantic import BaseModel, HttpUrl
from datetime import datetime
from db.models import SourceType

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
    url: HttpUrl
    summary: str
    source: Source
    published_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True
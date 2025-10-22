import uuid
from pydantic import BaseModel, HttpUrl
from typing import List, Optional
from datetime import datetime
from db.models import SourceType

# Import Content schema without creating circular imports
from content.schemas import Content

class SourceBase(BaseModel):
    """Base Source schema with common attributes"""
    type: SourceType
    url: HttpUrl
    name: Optional[str] = None

class Source(SourceBase):
    """Schema for source responses, including ID and a list of contents"""
    id: uuid.UUID
    original_id: str
    contents: Optional[List[Content]] = []
    
    class Config:
        from_attributes = True 

class SourceCreate(BaseModel):
    type: SourceType
    url: HttpUrl


class UserSourcesUpdate(BaseModel):
    sources: List[SourceCreate]

class UserSources(BaseModel):
    sources: List[Source]
    
    class Config:
        from_attributes = True 
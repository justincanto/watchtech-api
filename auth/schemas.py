from pydantic import BaseModel, EmailStr
from typing import Optional, List
import uuid
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    name: str
    picture: Optional[str] = None


class UserCreate(UserBase):
    google_id: str


class UserSessionBase(BaseModel):
    user_agent: Optional[str] = None


class UserSessionCreate(UserSessionBase):
    user_id: uuid.UUID
    token: str
    expires_at: datetime


class User(UserBase):
    id: uuid.UUID
    google_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserInDB(User):
    pass 
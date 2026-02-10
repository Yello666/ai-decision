from typing import Optional

from pydantic import BaseModel
from datetime import datetime


class ContentGenerateRequest(BaseModel):
    title: str
    prompt: str


class ContentOut(BaseModel):
    id: int
    shopify_store_id: str
    title: str
    prompt: str
    generated_text: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

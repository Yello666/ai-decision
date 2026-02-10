from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func
from .base import Base


class Content(Base):
    __tablename__ = "contents"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)
    generated_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

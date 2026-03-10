from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func
from .base import Base


class Hotspot(Base):
    """热点表：全局热点（如 YouTube 趋势），所有商家共享，不按店铺隔离。"""

    __tablename__ = "hotspots"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

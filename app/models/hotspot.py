from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class Hotspot(Base):
    """热点表：商户自行上传的热点，按 merchant_id 隔离。"""

    __tablename__ = "hotspots"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(
        Integer,
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    tags = Column(String(255), nullable=True, comment="标签，逗号分隔；可选")
    audience = Column(String(255), nullable=True, comment="受众画像，逗号分隔；可选")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

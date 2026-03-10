# 统一内容生成任务（视频/图片/文字），用于轮询状态与结果
from sqlalchemy import Column, DateTime, Integer, String, Text, JSON, func
from .base import Base


class Generation(Base):
    """内容生成任务表：视频/图片/文字生成及轮询，按店铺隔离。"""

    __tablename__ = "generations"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), index=True, nullable=False)  # 所属店铺，多租户隔离

    type = Column(String(16), nullable=False, index=True)  # video | image | text
    status = Column(String(32), nullable=False, default="pending", index=True)  # pending | processing | completed | failed

    prompt_used = Column(Text, nullable=False)  # 实际发给模型/API 的 prompt
    trend_snapshot = Column(JSON, nullable=True)   # TrendObject 快照
    brand_snapshot = Column(JSON, nullable=True)   # BrandObject 快照

    external_id = Column(String(128), nullable=True, index=True)  # 如 SeedDance video_id
    result_url = Column(Text, nullable=True)       # 视频/图片 URL
    result_text = Column(Text, nullable=True)      # 文字生成结果
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

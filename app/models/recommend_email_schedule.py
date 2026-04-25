from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from .base import Base


class MerchantHotspotRecommendEmailSchedule(Base):
    """商户热点推荐邮件定时配置。"""

    __tablename__ = "merchant_hotspot_recommend_email_schedule"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=False)
    mode = Column(String(16), nullable=False, default="interval_from_now")
    min_compatibility_score = Column(Numeric(5, 2), nullable=False, default=40.00)
    interval_hours = Column(Integer, nullable=False, default=24)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    send_hour = Column(Integer, nullable=False, default=9)
    send_minute = Column(Integer, nullable=False, default=0)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MerchantHotspotRecommendEmailDelivery(Base):
    """商户热点推荐邮件已发送记录，用于防重复发送。"""

    __tablename__ = "merchant_hotspot_recommend_email_delivery"
    __table_args__ = (
        UniqueConstraint("merchant_id", "brand_fp", "trend_fp", name="uq_mhred_merchant_brand_trend"),
    )

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    schedule_id = Column(
        Integer,
        ForeignKey("merchant_hotspot_recommend_email_schedule.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    platform = Column(String(32), nullable=False, default="youtube")
    trend_id = Column(String(128), nullable=False)
    brand_fp = Column(String(32), nullable=False)
    trend_fp = Column(String(32), nullable=False)
    compatibility_score = Column(Numeric(5, 2), nullable=False)
    min_score_at_send = Column(Numeric(5, 2), nullable=False)
    matched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

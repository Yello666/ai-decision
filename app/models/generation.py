# 统一内容生成任务（视频/图片/文字），用于轮询状态与结果
from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text, JSON, func
from .base import Base


GENERATION_STATUS_QUEUED = "queued"
GENERATION_STATUS_RUNNING = "running"
GENERATION_STATUS_SUCCEEDED = "succeeded"
GENERATION_STATUS_FAILED = "failed"
GENERATION_STATUS_EXPIRED = "expired"
GENERATION_STATUS_CANCELLED = "cancelled"

GENERATION_STATUSES = (
    GENERATION_STATUS_QUEUED,
    GENERATION_STATUS_RUNNING,
    GENERATION_STATUS_SUCCEEDED,
    GENERATION_STATUS_FAILED,
    GENERATION_STATUS_EXPIRED,
    GENERATION_STATUS_CANCELLED,
)
GENERATION_TERMINAL_STATUSES = (
    GENERATION_STATUS_SUCCEEDED,
    GENERATION_STATUS_FAILED,
    GENERATION_STATUS_EXPIRED,
    GENERATION_STATUS_CANCELLED,
)


class Generation(Base):
    """内容生成任务表：视频/图片/文字生成及轮询，按店铺隔离。"""

    __tablename__ = "generations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_generations_status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), index=True, nullable=False)  # 所属店铺，多租户隔离

    type = Column(String(16), nullable=False, index=True)  # video | text
    status = Column(
        String(32),
        nullable=False,
        default=GENERATION_STATUS_QUEUED,
        index=True,
    )  # queued | running | succeeded | failed | expired | cancelled
    thread_id = Column(String(64), nullable=True, index=True)  # 所属视频生成会话 ID（仅视频 thread 任务有值）
    segment_id = Column(Integer, nullable=True, index=True)  # 分镜段 ID（与 LangGraph task_results 对齐；串行链预创建占位记录时使用）

    prompt_used = Column(Text, nullable=False)  # 实际发给模型/API 的 prompt
    trend_snapshot = Column(JSON, nullable=True)   # TrendObject 快照
    brand_snapshot = Column(JSON, nullable=True)   # BrandObject 快照

    external_id = Column(String(128), nullable=True, index=True)  # 如 SeedDance video_id
    result_url = Column(Text, nullable=True)       # 视频/图片 URL
    result_text = Column(Text, nullable=True)      # 文字生成结果
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

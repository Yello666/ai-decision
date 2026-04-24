"""视频生成会话索引表。

配合 LangGraph 的 Postgres checkpointer 使用：
- ``checkpoints`` 表（Postgres）保存每一步的完整 state 快照，支持 ``aget_state`` 回放完整对话。
- ``video_threads`` 表（MySQL）维护轻量索引：归属店铺、状态、标题、时间，支持按店铺分页列表查询。

两者通过 ``thread_id`` 关联。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from .base import Base


class VideoThread(Base):
    """视频生成会话索引，一条 = 一个 thread。"""

    __tablename__ = "video_threads"

    # LangGraph thread_id（UUID4 字符串）
    thread_id = Column(String(64), primary_key=True)

    # 多租户隔离
    shopify_store_id = Column(String(64), index=True, nullable=False)

    # 生命周期状态：running / waiting_human / finished / error
    status = Column(String(32), index=True, nullable=False, default="running")
    # 对应 VideoGenerationState.current_step，便于列表直观展示当前阶段
    current_step = Column(String(64), nullable=True)

    # 标题：取 user_input 前 100 字符；列表页展示用，避免加载 Text 大字段
    title = Column(String(255), nullable=True)
    user_input = Column(Text, nullable=True)

    # 列表页缩略图：创建时优先用 product.image_url，其次 media_assets 首帧/参考图
    thumbnail_url = Column(Text, nullable=True)

    # 剧本改写轮次
    revision_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # 仅在状态流转为 finished / error 时被写入
    completed_at = Column(DateTime(timezone=True), nullable=True)

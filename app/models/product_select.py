from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    DECIMAL,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from .base import Base


class ProductSelectMonitor(Base):
    """Product Select 监控对象/监控池。"""

    __tablename__ = "product_select_monitors"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "platform",
            "handle",
            "monitor_type",
            name="uk_psm_scope",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    platform = Column(String(32), nullable=False, index=True)
    handle = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    score = Column(DECIMAL(4, 2), nullable=False, default=5)
    monitor_type = Column(String(32), nullable=False, default="profile")
    is_enabled = Column(Boolean, nullable=False, default=True, index=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ProductSelectContent(Base):
    """Product Select 采集内容：Instagram 帖子或 YouTube 视频。"""

    __tablename__ = "product_select_contents"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uk_psc_platform_external"),
    )

    id = Column(Integer, primary_key=True, index=True)
    monitor_id = Column(
        Integer,
        ForeignKey("product_select_monitors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    merchant_id = Column(Integer, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    platform = Column(String(32), nullable=False, index=True)
    external_id = Column(String(255), nullable=False)
    url = Column(Text, nullable=True)
    caption_or_title = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    raw_path = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="fetched", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ProductSelectImage(Base):
    """Product Select 图片资产：原图、抽帧、裁剪图。"""

    __tablename__ = "product_select_images"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(
        Integer,
        ForeignKey("product_select_contents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    image_type = Column(String(32), nullable=False, index=True)
    local_path = Column(Text, nullable=True)
    oss_key = Column(Text, nullable=True)
    oss_url = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProductSelectObject(Base):
    """Product Select 识图物件/商品机会。"""

    __tablename__ = "product_select_objects"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(
        Integer,
        ForeignKey("product_select_contents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_image_id = Column(
        Integer,
        ForeignKey("product_select_images.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    crop_image_id = Column(
        Integer,
        ForeignKey("product_select_images.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category = Column(String(128), nullable=False, index=True)
    related_ip = Column(String(255), nullable=True, index=True)
    description = Column(Text, nullable=True)
    attributes_json = Column(JSON, nullable=True)
    ecommerce_potential = Column(String(16), nullable=False, default="medium", index=True)
    reason = Column(Text, nullable=True)
    bbox_json = Column(JSON, nullable=True)
    token_usage_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProductSelectMatch(Base):
    """Product Select 同款/商品匹配结果。"""

    __tablename__ = "product_select_matches"

    id = Column(Integer, primary_key=True, index=True)
    object_id = Column(
        Integer,
        ForeignKey("product_select_objects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source = Column(String(64), nullable=False, index=True)
    match_level = Column(String(32), nullable=True, index=True)
    title = Column(String(512), nullable=True)
    store = Column(String(255), nullable=True)
    url = Column(Text, nullable=True)
    price = Column(DECIMAL(12, 2), nullable=True, index=True)
    currency = Column(String(16), nullable=True)
    rating = Column(DECIMAL(4, 2), nullable=True)
    reviews = Column(Integer, nullable=True)
    in_stock = Column(Boolean, nullable=True)
    thumbnail_url = Column(Text, nullable=True)
    raw_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


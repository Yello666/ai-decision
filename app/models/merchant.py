from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func
from .base import Base


class Merchant(Base):
    """商家表：Shopify OAuth 商户与平台自注册商户。"""

    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), unique=True, index=True, nullable=False)  # 租户 ID；Shopify 时为店铺数字 ID
    shopify_domain = Column(String(255), nullable=True)  # 店铺域名，如 mystore.myshopify.com
    shopify_category = Column(String(128), nullable=True)  # 店铺类目
    name = Column(String(128), nullable=False)  # 显示名称，亦为登录用户名
    email = Column(String(128), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    shopify_access_token = Column(String(512), nullable=True)  # Shopify OAuth token，用于代表店铺调用 API
    account_type = Column(String(32), nullable=False, server_default="shopify")  # shopify | standalone
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

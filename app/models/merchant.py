from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func
from .base import Base


class Merchant(Base):
    """商家表：平台注册的 Shopify 商户，与 Shopify 店铺一一对应。"""

    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), unique=True, index=True, nullable=False)  # Shopify 店铺数字 ID，系统内唯一标识
    shopify_domain = Column(String(255), nullable=True)  # 店铺域名，如 mystore.myshopify.com
    shopify_category = Column(String(128), nullable=True)  # 店铺类目
    name = Column(String(128), nullable=False)  # 显示名称，亦为登录用户名
    email = Column(String(128), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    shopify_access_token = Column(String(512), nullable=True)  # Shopify OAuth token，用于代表店铺调用 API
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

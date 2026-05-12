from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from .base import Base


class MerchantLocalProduct(Base):
    """自注册商户（standalone）的商品，替代 Shopify Admin API 商品源。"""

    __tablename__ = "merchant_local_products"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    merchant_id = Column(
        Integer,
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    size_description = Column(String(255), nullable=True)
    price = Column(Numeric(12, 2), nullable=False, default=0)
    compare_at_price = Column(Numeric(12, 2), nullable=True)
    image_url = Column(String(2048), nullable=True)
    inventory = Column(Integer, nullable=False, default=0)
    product_type = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, server_default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

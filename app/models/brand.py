from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.models import Base


class Brand(Base):
    """品牌表：商户配置的品牌信息，与 merchants 一对多。"""

    __tablename__ = "brand"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), index=True, nullable=False)  # 所属店铺，便于按店铺查
    merchant_id = Column(Integer, index=True, nullable=False)  # 所属商户

    name= Column(String(64), index=True, nullable=False,comment="品牌名称")
    mainly_sold_products= Column(String(64), index=False, nullable=False,comment="品牌主要售卖商品")
    core_value = Column(String(64), index=False, nullable=False, comment="品牌目标或介绍")
    tone= Column(String(64), index=False, nullable=False,comment="品牌风格")
    audience= Column(String(64), index=False,comment="品牌目标受众")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

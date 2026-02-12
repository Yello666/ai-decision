from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.models import Base


class Brand(Base):
    __tablename__ = "brand"

    id = Column(Integer, primary_key=True, index=True)
    shopify_store_id = Column(String(64), index=True, nullable=False)
    merchant_id = Column(Integer, index=True,nullable=False)

    name= Column(String(64), index=True, nullable=False,comment="品牌名称")
    core_value= Column(String(64), index=False, nullable=False,comment="品牌核心价值，目标")
    industry= Column(String(64), index=False, nullable=False,comment="品牌所属行业")
    tone= Column(String(64), index=False, nullable=False,comment="品牌调性")
    audience= Column(String(64), index=False,comment="品牌目标受众")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

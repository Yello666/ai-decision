from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LocalProductCreate(BaseModel):
    """新建本地商品（请求体）。"""

    title: str = Field(..., min_length=1, max_length=512)
    description: Optional[str] = None
    price: float = Field(default=0, ge=0)
    compare_at_price: Optional[float] = Field(default=None, ge=0)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    inventory: int = Field(default=0, ge=0)
    product_type: Optional[str] = Field(default=None, max_length=128)
    status: str = Field(default="active", max_length=32)


class LocalProductUpdate(BaseModel):
    """更新本地商品：未传的字段不改。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=512)
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    compare_at_price: Optional[float] = Field(default=None, ge=0)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    inventory: Optional[int] = Field(default=None, ge=0)
    product_type: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = Field(default=None, max_length=32)


class LocalProductOut(BaseModel):
    """单条商品返回（含库表主键 id，与 /products 中 product_id 一致）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    price: float
    compare_at_price: Optional[float] = None
    image_url: Optional[str] = None
    inventory: int
    product_type: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

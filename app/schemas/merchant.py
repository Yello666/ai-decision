from typing import Optional
from pydantic import BaseModel, ConfigDict


class MerchantBase(BaseModel):
    """商户展示字段（不含 password_hash、shopify_access_token，接口不返回敏感信息）。"""

    name: str
    email: str
    shopify_store_id: str
    shopify_domain: Optional[str] = None
    shopify_category: Optional[str] = None
    is_active: bool = True


class MerchantCreate(BaseModel):
    name: str
    email: str
    password: str
    shopify_domain: str


class MerchantUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    shopify_category: Optional[str] = None


class MerchantOut(MerchantBase):
    model_config = ConfigDict(from_attributes=True)
    id: int

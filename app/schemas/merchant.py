from typing import Optional
from pydantic import BaseModel, ConfigDict


class MerchantBase(BaseModel):
    """商户展示字段（不含 password_hash、shopify_access_token，接口不返回敏感信息）。"""

    name: str
    email: str
    shopify_store_id: str
    shopify_domain: Optional[str] = None
    shopify_category: Optional[str] = None
    account_type: str = "shopify"
    is_active: bool = True


class MerchantCreate(BaseModel):
    name: str
    email: str
    password: str
    shopify_domain: str


class MerchantLocalCreate(BaseModel):
    """平台自注册（无 Shopify OAuth）：与 MerchantCreate 区分，不要求 shopify_domain。"""

    name: str
    email: str
    password: str


class MerchantOut(MerchantBase):
    model_config = ConfigDict(from_attributes=True)
    id: int

from typing import Optional
from pydantic import BaseModel


class MerchantBase(BaseModel):
    name: str
    email: str
    shopify_store_id: str
    shopify_domain: Optional[str] = None
    shopify_category: Optional[str] = None
    brand_tone: Optional[str] = None
    preferences: Optional[str] = None
    is_active: bool = True


class MerchantCreate(BaseModel):
    name: str
    email: str
    password: str
    shopify_domain: str


class MerchantUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    brand_tone: Optional[str] = None
    preferences: Optional[str] = None
    shopify_category: Optional[str] = None


class MerchantOut(MerchantBase):
    id: int

    class Config:
        from_attributes = True


class BrandToneUpdate(BaseModel):
    brand_tone: str

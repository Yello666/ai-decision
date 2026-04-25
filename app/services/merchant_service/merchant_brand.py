from typing import Optional, Any, Dict

from sqlalchemy.orm import Session
from app.models import Merchant, Brand


def get_merchant_by_store_id(db: Session, shopify_store_id: str) -> Optional[Merchant]:
    return (
        db.query(Merchant)
        .filter(Merchant.shopify_store_id == shopify_store_id)
        .first()
    )


def get_brand_by_merchant_id(db: Session, merchant_id: int) -> Optional[Brand]:
    return db.query(Brand).filter(Brand.merchant_id == merchant_id).first()


def create_or_update_brand(db: Session, merchant: Merchant, brand_data: Dict[str, Any]) -> Brand:
    brand = get_brand_by_merchant_id(db, merchant.id)
    
    # 处理 audience 列表转字符串
    if "audience" in brand_data and isinstance(brand_data["audience"], list):
        brand_data["audience"] = ",".join(brand_data["audience"])
    
    if not brand:
        brand = Brand(
            shopify_store_id=merchant.shopify_store_id,
            merchant_id=merchant.id,
            **brand_data
        )
        db.add(brand)
    else:
        for key, value in brand_data.items():
            setattr(brand, key, value)
    
    db.commit()
    db.refresh(brand)
    return brand

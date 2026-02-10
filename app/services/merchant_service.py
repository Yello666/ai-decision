from typing import Optional

from sqlalchemy.orm import Session
from app.models import Merchant


def get_merchant_by_store_id(db: Session, shopify_store_id: str) -> Optional[Merchant]:
    return (
        db.query(Merchant)
        .filter(Merchant.shopify_store_id == shopify_store_id)
        .first()
    )

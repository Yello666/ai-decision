"""按商户 account_type 选择 Shopify API 或本地 merchant_local_products。"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models import Merchant

from . import shopify_products as shopify
from .local_products import get_local_product, list_local_products

_STANDALONE = "standalone"


def _with_mysql_session(fn):
    from app.db.mysql import SessionLocal

    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def _is_standalone(merchant: Merchant) -> bool:
    return (merchant.account_type or "") == _STANDALONE


async def fetch_products(
    merchant: Merchant,
    *,
    limit: int = 50,
    since_id: Optional[int] = None,
    product_type: Optional[str] = None,
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
) -> List[Any]:
    if _is_standalone(merchant):
        return await asyncio.to_thread(
            _with_mysql_session,
            lambda db: list_local_products(
                db,
                merchant.id,
                limit=limit,
                since_id=since_id,
                product_type=product_type,
                status=status,
                collection_id=collection_id,
            ),
        )
    return await shopify.fetch_products(
        merchant,
        limit=limit,
        since_id=since_id,
        product_type=product_type,
        status=status,
        collection_id=collection_id,
    )


async def fetch_product(merchant: Merchant, product_id: int) -> Any:
    if _is_standalone(merchant):
        product = await asyncio.to_thread(
            _with_mysql_session,
            lambda db: get_local_product(db, merchant.id, product_id),
        )
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product
    return await shopify.fetch_product(merchant, product_id)


async def update_product_prices(
    merchant: Merchant,
    product_id: int,
    new_price: float,
    *,
    compare_at_price: Optional[float] = None,
) -> Dict[str, Any]:
    if _is_standalone(merchant):
        raise HTTPException(
            status_code=400,
            detail="standalone_merchant_price_update_not_supported",
        )
    return await shopify.update_product_prices(
        merchant,
        product_id,
        new_price,
        compare_at_price=compare_at_price,
    )


async def update_variant_price(
    merchant: Merchant,
    product_id: int,
    variant_id: int,
    new_price: float,
    *,
    compare_at_price: Optional[float] = None,
) -> Dict[str, Any]:
    if _is_standalone(merchant):
        raise HTTPException(
            status_code=400,
            detail="standalone_merchant_price_update_not_supported",
        )
    return await shopify.update_variant_price(
        merchant,
        product_id,
        variant_id,
        new_price,
        compare_at_price=compare_at_price,
    )

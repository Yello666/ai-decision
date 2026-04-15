from fastapi import APIRouter, Depends

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.schemas.product import GetProductListRequest, UpdatePriceRequest
from app.services.product_service import (
    fetch_product,
    fetch_products,
    update_product_prices,
    update_variant_price,
)

router = APIRouter(prefix="/products", tags=["products"])


@router.get("")
async def list_products(
    request: GetProductListRequest = Depends(),
    merchant: Merchant = Depends(get_current_merchant),
):
    products = await fetch_products(
        merchant,
        limit=request.limit,
        since_id=request.since_id,
        product_type=request.product_type,
        status=request.status,
        collection_id=request.collection_id,
    )
    return success(data=products)


@router.get("/{product_id}")
async def get_product(
    product_id: int,
    merchant: Merchant = Depends(get_current_merchant),
):
    product = await fetch_product(merchant, product_id)
    return success(data=product)


@router.put("/{product_id}/price")
async def update_all_variant_prices(
    product_id: int,
    body: UpdatePriceRequest,
    merchant: Merchant = Depends(get_current_merchant),
):
    """修改商品所有 variant 的价格。"""
    result = await update_product_prices(
        merchant,
        product_id,
        body.price,
        compare_at_price=body.compare_at_price,
    )
    return success(data=result)


@router.put("/{product_id}/variants/{variant_id}/price")
async def update_single_variant_price(
    product_id: int,
    variant_id: int,
    body: UpdatePriceRequest,
    merchant: Merchant = Depends(get_current_merchant),
):
    """修改商品指定 variant 的价格。"""
    result = await update_variant_price(
        merchant,
        product_id,
        variant_id,
        body.price,
        compare_at_price=body.compare_at_price,
    )
    return success(data=result)

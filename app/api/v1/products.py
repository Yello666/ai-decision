from fastapi import APIRouter, Depends

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.schemas.product import GetProductListRequest
from app.services.product_service import fetch_product, fetch_products

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

from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.core.config import get_settings
from app.models import Merchant
from app.schemas.product import ProductObject, ProductOut, convert_shopify_product

settings = get_settings()


def _shopify_headers(access_token: str) -> Dict[str, str]:
    return {"X-Shopify-Access-Token": access_token}


def _base_url(domain: str) -> str:
    return f"https://{domain}/admin/api/{settings.SHOPIFY_API_VERSION}"


def _validate_shopify_credentials(merchant: Merchant) -> None:
    # Avoid passing None into request headers/URL and return clear API errors.
    if not merchant.shopify_domain:
        raise HTTPException(status_code=400, detail="shopify_domain_not_configured")
    if not merchant.shopify_access_token:
        raise HTTPException(status_code=400, detail="shopify_access_token_not_configured")


async def fetch_products(
    merchant: Merchant,
    *,
    limit: int = 50,
    since_id: Optional[int] = None,
    product_type: Optional[str] = None,
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
) -> List[ProductObject]:
    _validate_shopify_credentials(merchant)

    params: Dict[str, Any] = {"limit": min(limit, 250)}
    if since_id is not None:
        params["since_id"] = since_id
    if product_type is not None:
        params["product_type"] = product_type
    if status is not None:
        params["status"] = status
    if collection_id is not None:
        params["collection_id"] = collection_id

    url = f"{_base_url(merchant.shopify_domain)}/products.json"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            url,
            headers=_shopify_headers(merchant.shopify_access_token),
            params=params,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Shopify API error: {resp.text}",
            )
        data = resp.json()

    raw_products = [ProductOut.model_validate(p) for p in data.get("products", [])]
    return [convert_shopify_product(p) for p in raw_products]


async def fetch_product(merchant: Merchant, product_id: int) -> ProductObject:
    _validate_shopify_credentials(merchant)

    url = f"{_base_url(merchant.shopify_domain)}/products/{product_id}.json"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            url,
            headers=_shopify_headers(merchant.shopify_access_token),
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Product not found")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Shopify API error: {resp.text}",
            )
        data = resp.json()

    return convert_shopify_product(ProductOut.model_validate(data["product"]))

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.core.config import get_settings
from app.models import Merchant
from app.schemas.product import ProductObject, ProductOut, convert_shopify_product

logger = logging.getLogger(__name__)
settings = get_settings()


def _shopify_headers(access_token: str) -> Dict[str, str]:
    return {"X-Shopify-Access-Token": access_token}


def _base_url(domain: str) -> str:
    return f"https://{domain}/admin/api/{settings.SHOPIFY_API_VERSION}"


def _graphql_url(domain: str) -> str:
    return f"https://{domain}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"


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


_VARIANTS_BULK_UPDATE_MUTATION = """
mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
    productVariantsBulkUpdate(productId: $productId, variants: $variants) {
        product { id }
        productVariants {
            id
            price
        }
        userErrors {
            field
            message
        }
    }
}
"""


async def _call_variants_bulk_update(
    merchant: Merchant,
    product_id: int,
    gql_variants: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """发送 productVariantsBulkUpdate GraphQL mutation 并返回结果。"""
    variables = {
        "productId": f"gid://shopify/Product/{product_id}",
        "variants": gql_variants,
    }

    # Step 3: 调用 GraphQL API
    graphql_endpoint = _graphql_url(merchant.shopify_domain)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            graphql_endpoint,
            headers={
                **_shopify_headers(merchant.shopify_access_token),
                "Content-Type": "application/json",
            },
            json={"query": _VARIANTS_BULK_UPDATE_MUTATION, "variables": variables},
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Shopify GraphQL error: {resp.text}",
            )
        body = resp.json()

    if "errors" in body:
        raise HTTPException(
            status_code=502,
            detail=f"Shopify GraphQL errors: {body['errors']}",
        )

    mutation_result = body.get("data", {}).get("productVariantsBulkUpdate", {})
    user_errors = mutation_result.get("userErrors", [])
    if user_errors:
        raise HTTPException(
            status_code=400,
            detail=f"Shopify variant update failed: {user_errors}",
        )
    return mutation_result


def _build_variant_entry(
    variant_id: int,
    price: float,
    compare_at_price: Optional[float] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": f"gid://shopify/ProductVariant/{variant_id}",
        "price": f"{price:.2f}",
    }
    if compare_at_price is not None:
        entry["compareAtPrice"] = f"{compare_at_price:.2f}"
    return entry


async def update_product_prices(
    merchant: Merchant,
    product_id: int,
    new_price: float,
    *,
    compare_at_price: Optional[float] = None,
) -> Dict[str, Any]:
    """批量更新商品 **所有** variant 的价格。

    通过 REST 获取全部 variant ID，再用 GraphQL productVariantsBulkUpdate 一次性写入。
    """
    _validate_shopify_credentials(merchant)

    rest_url = f"{_base_url(merchant.shopify_domain)}/products/{product_id}.json"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            rest_url,
            headers=_shopify_headers(merchant.shopify_access_token),
            params={"fields": "id,variants"},
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Product not found")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Shopify API error fetching product variants: {resp.text}",
            )
        product_data = resp.json().get("product", {})

    raw_variants = product_data.get("variants", [])
    if not raw_variants:
        raise HTTPException(status_code=400, detail="Product has no variants to update")

    gql_variants = [
        _build_variant_entry(v["id"], new_price, compare_at_price)
        for v in raw_variants
    ]

    result = await _call_variants_bulk_update(merchant, product_id, gql_variants)
    logger.info(
        "Updated all %d variant(s) for product %d to price %.2f",
        len(gql_variants), product_id, new_price,
    )
    return result


async def update_variant_price(
    merchant: Merchant,
    product_id: int,
    variant_id: int,
    new_price: float,
    *,
    compare_at_price: Optional[float] = None,
) -> Dict[str, Any]:
    """更新商品的 **单个** variant 的价格。"""
    _validate_shopify_credentials(merchant)

    gql_variants = [_build_variant_entry(variant_id, new_price, compare_at_price)]
    result = await _call_variants_bulk_update(merchant, product_id, gql_variants)
    logger.info(
        "Updated variant %d of product %d to price %.2f",
        variant_id, product_id, new_price,
    )
    return result



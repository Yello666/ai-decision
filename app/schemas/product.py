import re
from datetime import datetime
from typing import List, Optional

from fastapi import Query
from pydantic import BaseModel, Field


# ==========================================
# Shopify 原始数据模型（用于解析 Shopify API 响应）
# ==========================================

class ProductImageOut(BaseModel):
    id: int
    product_id: int
    position: int = 0
    src: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    alt: Optional[str] = None
    variant_ids: List[int] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProductOptionOut(BaseModel):
    id: int
    product_id: int
    name: str
    position: int = 1
    values: List[str] = Field(default_factory=list)


class ProductVariantOut(BaseModel):
    id: int
    product_id: int
    title: Optional[str] = None
    price: Optional[str] = None
    compare_at_price: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    position: int = 1
    option1: Optional[str] = None
    option2: Optional[str] = None
    option3: Optional[str] = None
    inventory_quantity: Optional[int] = None
    inventory_management: Optional[str] = None
    inventory_policy: Optional[str] = None
    inventory_item_id: Optional[int] = None
    weight: Optional[float] = None
    weight_unit: Optional[str] = None
    grams: Optional[int] = None
    taxable: Optional[bool] = None
    requires_shipping: Optional[bool] = None
    fulfillment_service: Optional[str] = None
    image_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProductOut(BaseModel):
    id: int
    title: Optional[str] = None
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    handle: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[str] = None
    published_at: Optional[str] = None
    published_scope: Optional[str] = None
    template_suffix: Optional[str] = None
    variants: List[ProductVariantOut] = Field(default_factory=list)
    options: List[ProductOptionOut] = Field(default_factory=list)
    images: List[ProductImageOut] = Field(default_factory=list)
    image: Optional[ProductImageOut] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ==========================================
# 请求结构体
# ==========================================

class GetProductListRequest:
    """商品列表查询参数"""

    def __init__(
        self,
        # 每页返回的商品数量，最小 1，最大 250
        limit: int = Query(default=50, ge=1, le=250, description="每页返回的商品数量，最小 1，最大 250"),
        # 返回 ID 大于此值的商品，用于游标分页
        since_id: Optional[int] = Query(default=None, description="返回 ID 大于此值的商品，用于游标分页"),
        # 按商品类型筛选，如 "T-Shirt"、"Shoes" 等
        product_type: Optional[str] = Query(default=None, description="按商品类型筛选，如 T-Shirt、Shoes 等"),
        # 按商品状态筛选，可选值：active（上架）、draft（草稿）、archived（已归档）
        status: Optional[str] = Query(default=None, description="按商品状态筛选，可选值：active、draft、archived"),
        # 按商品集合 ID 筛选，只返回属于该集合的商品
        collection_id: Optional[int] = Query(default=None, description="按商品集合 ID 筛选，只返回属于该集合的商品"),
    ):
        self.limit = limit
        self.since_id = since_id
        self.product_type = product_type
        self.status = status
        self.collection_id = collection_id


# ==========================================
# 响应结构体
# ==========================================

class VariantObject(BaseModel):
    """商品规格信息"""
    name: str = Field(..., description="规格名称，如 S / Red 等")
    price: float = Field(..., description="规格价格（若有折扣则为折后价）")
    image_url: str = Field(default="", description="规格对应的图片 URL")


class ProductObject(BaseModel):
    """商品列表项（精简结构）"""
    name: str = Field(..., description="商品名称")
    description: str = Field(..., description="商品描述（纯文本，已去除 HTML 标签）")
    price: float = Field(..., description="商品价格（若有折扣则为折后价）")
    image_url: str = Field(default="", description="商品主图 URL")
    variants: Optional[List[VariantObject]] = Field(
        default=None,
        description="商品规格列表；仅当商品有多个规格时返回",
    )


# ==========================================
# 转换工具函数
# ==========================================

def _strip_html(html: Optional[str]) -> str:
    """移除 HTML 标签，返回纯文本"""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", "", html)
    return text.strip()


def _get_primary_image_url(product: ProductOut) -> str:
    """获取商品主图 URL：优先使用 image 字段，其次取 images 中 position 最小的"""
    if product.image and product.image.src:
        return product.image.src
    if product.images:
        primary = min(product.images, key=lambda img: img.position)
        return primary.src or ""
    return ""


def _resolve_variant_price(variant: ProductVariantOut) -> float:
    """
    获取规格的实际售价。
    Shopify 中 price 是当前售价，compare_at_price 是原价（划线价）。
    若商品降价，price < compare_at_price，我们直接取 price。
    """
    return float(variant.price or 0)


def _find_variant_image_url(variant: ProductVariantOut, product: ProductOut, fallback: str) -> str:
    """根据 variant.image_id 查找对应图片，找不到则回退到商品主图"""
    if variant.image_id:
        for img in product.images:
            if img.id == variant.image_id:
                return img.src or fallback
    return fallback


def convert_shopify_product(product: ProductOut) -> ProductObject:
    """将 Shopify 原始商品数据转换为精简的 ProductObject"""
    image_url = _get_primary_image_url(product)
    description = _strip_html(product.body_html)

    if len(product.variants) <= 1:
        price = _resolve_variant_price(product.variants[0]) if product.variants else 0.0
        return ProductObject(
            name=product.title or "",
            description=description,
            price=price,
            image_url=image_url,
        )

    variant_objects = [
        VariantObject(
            name=v.title or "",
            price=_resolve_variant_price(v),
            image_url=_find_variant_image_url(v, product, image_url),
        )
        for v in product.variants
    ]
    min_price = min(v.price for v in variant_objects)

    return ProductObject(
        name=product.title or "",
        description=description,
        price=min_price,
        image_url=image_url,
        variants=variant_objects,
    )

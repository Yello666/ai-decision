"""自注册商户（standalone）本地商品 CRUD。

仅 ``account_type=standalone`` 的登录用户可访问；Shopify 商户返回 403。
商品数据表：``merchant_local_products``；读侧与 ``GET /products`` 共用同一数据源逻辑。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.local_product import LocalProductCreate, LocalProductUpdate
from app.services.product_service.local_products import (
    create_for_merchant,
    delete_for_merchant,
    get_for_merchant,
    list_for_merchant,
    update_for_merchant,
)

router = APIRouter(prefix="/local-products", tags=["local-products"])


def _standalone_merchant(merchant: Merchant = Depends(get_current_merchant)) -> Merchant:
    """限制：只有平台自注册商户可管理本地商品表。"""
    if (merchant.account_type or "") != "standalone":
        raise HTTPException(status_code=403, detail="standalone_merchants_only")
    return merchant


# --- 查：列表（支持 limit / since_id / 类型 / 状态，与公开商品列表习惯一致）---


@router.get("")
def list_local_products(
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(_standalone_merchant),
    limit: int = Query(50, ge=1, le=250, description="每页条数"),
    since_id: Optional[int] = Query(None, description="只返回 id 大于该值的记录，游标分页"),
    product_type: Optional[str] = Query(None, description="按 product_type 筛选"),
    status: Optional[str] = Query(None, description="按 status 筛选，如 active"),
):
    data = list_for_merchant(
        db,
        merchant.id,
        limit=limit,
        since_id=since_id,
        product_type=product_type,
        status=status,
    )
    return success(data=data)


# --- 查：单条 ---


@router.get("/{product_id}")
def get_local_product(
    product_id: int,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(_standalone_merchant),
):
    row = get_for_merchant(db, merchant.id, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    return success(data=row)


# --- 增 ---


@router.post("")
def create_local_product(
    body: LocalProductCreate,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(_standalone_merchant),
):
    row = create_for_merchant(db, merchant.id, body)
    return success(data=row)


# --- 改（部分字段可选，未传的不更新）---


@router.put("/{product_id}")
def update_local_product(
    product_id: int,
    body: LocalProductUpdate,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(_standalone_merchant),
):
    row = update_for_merchant(db, merchant.id, product_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    return success(data=row)


# --- 删 ---


@router.delete("/{product_id}")
def delete_local_product(
    product_id: int,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(_standalone_merchant),
):
    ok = delete_for_merchant(db, merchant.id, product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="product_not_found")
    return success(data={"id": product_id, "deleted": True})

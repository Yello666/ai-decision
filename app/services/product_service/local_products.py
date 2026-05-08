"""自注册商户（standalone）商品：merchant_local_products 的读、写与 ProductObject 转换。"""

from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.merchant_local_product import MerchantLocalProduct
from app.schemas.local_product import LocalProductCreate, LocalProductOut, LocalProductUpdate
from app.schemas.product import ProductObject


def _query_rows(
    db: Session,
    merchant_id: int,
    *,
    limit: int = 50,
    since_id: Optional[int] = None,
    product_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[MerchantLocalProduct]:
    cap = min(max(limit, 1), 250)
    q = db.query(MerchantLocalProduct).filter(MerchantLocalProduct.merchant_id == merchant_id)
    if since_id is not None:
        q = q.filter(MerchantLocalProduct.id > since_id)
    if product_type is not None:
        q = q.filter(MerchantLocalProduct.product_type == product_type)
    if status is not None:
        q = q.filter(MerchantLocalProduct.status == status)
    return q.order_by(MerchantLocalProduct.id.asc()).limit(cap).all()


def _get_row(db: Session, merchant_id: int, product_id: int) -> Optional[MerchantLocalProduct]:
    return (
        db.query(MerchantLocalProduct)
        .filter(
            MerchantLocalProduct.merchant_id == merchant_id,
            MerchantLocalProduct.id == product_id,
        )
        .first()
    )


def _row_to_product_object(row: MerchantLocalProduct) -> ProductObject:
    return ProductObject(
        product_id=row.id,
        name=row.title or "",
        description=row.description or "",
        price=float(row.price or 0),
        image_url=row.image_url or "",
        inventory=int(row.inventory or 0),
    )


def _row_to_out(row: MerchantLocalProduct) -> LocalProductOut:
    return LocalProductOut.model_validate(row)


# --- 读：供 catalog（与 Shopify 统一的 ProductObject）---


def list_local_products(
    db: Session,
    merchant_id: int,
    *,
    limit: int = 50,
    since_id: Optional[int] = None,
    product_type: Optional[str] = None,
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
) -> List[ProductObject]:
    """与 Shopify 列表语义对齐：since_id 表示只取 id 更大的记录；collection_id 忽略。"""
    _ = collection_id
    rows = _query_rows(
        db,
        merchant_id,
        limit=limit,
        since_id=since_id,
        product_type=product_type,
        status=status,
    )
    return [_row_to_product_object(r) for r in rows]


def get_local_product(db: Session, merchant_id: int, product_id: int) -> Optional[ProductObject]:
    row = _get_row(db, merchant_id, product_id)
    return _row_to_product_object(row) if row else None


# --- CRUD：供 /local-products API（LocalProductOut）---


def list_for_merchant(
    db: Session,
    merchant_id: int,
    *,
    limit: int = 50,
    since_id: Optional[int] = None,
    product_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[LocalProductOut]:
    rows = _query_rows(
        db,
        merchant_id,
        limit=limit,
        since_id=since_id,
        product_type=product_type,
        status=status,
    )
    return [_row_to_out(r) for r in rows]


def get_for_merchant(db: Session, merchant_id: int, product_id: int) -> Optional[LocalProductOut]:
    row = _get_row(db, merchant_id, product_id)
    return _row_to_out(row) if row else None


def create_for_merchant(db: Session, merchant_id: int, body: LocalProductCreate) -> LocalProductOut:
    row = MerchantLocalProduct(
        merchant_id=merchant_id,
        title=body.title.strip(),
        description=body.description,
        price=Decimal(str(body.price)),
        compare_at_price=Decimal(str(body.compare_at_price)) if body.compare_at_price is not None else None,
        image_url=body.image_url,
        inventory=body.inventory,
        product_type=body.product_type,
        status=body.status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


def update_for_merchant(
    db: Session,
    merchant_id: int,
    product_id: int,
    body: LocalProductUpdate,
) -> Optional[LocalProductOut]:
    row = _get_row(db, merchant_id, product_id)
    if not row:
        return None

    patch = body.model_dump(exclude_unset=True)
    if "title" in patch:
        row.title = patch["title"].strip()
    if "description" in patch:
        row.description = patch["description"]
    if "price" in patch and patch["price"] is not None:
        row.price = Decimal(str(patch["price"]))
    if "compare_at_price" in patch:
        if patch["compare_at_price"] is None:
            row.compare_at_price = None
        else:
            row.compare_at_price = Decimal(str(patch["compare_at_price"]))
    if "image_url" in patch:
        row.image_url = patch["image_url"]
    if "inventory" in patch and patch["inventory"] is not None:
        row.inventory = patch["inventory"]
    if "product_type" in patch:
        row.product_type = patch["product_type"]
    if "status" in patch and patch["status"] is not None:
        row.status = patch["status"]

    db.commit()
    db.refresh(row)
    return _row_to_out(row)


def delete_for_merchant(db: Session, merchant_id: int, product_id: int) -> bool:
    row = _get_row(db, merchant_id, product_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True

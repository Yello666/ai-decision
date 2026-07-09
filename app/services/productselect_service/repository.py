"""Product Select 数据库写入/查询服务层。

这一层只负责把「监控对象、采集内容、图片、识图物件、商品匹配」这些结构化核心字段
落到 MySQL。大 JSON、图片文件本身仍保留在本地/OSS，通过 path/key/url 字段引用。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.product_select import (
    ProductSelectContent,
    ProductSelectImage,
    ProductSelectMatch,
    ProductSelectMonitor,
    ProductSelectObject,
    ProductSelectObjectProfile,
)


def _commit_refresh(db: Session, row: Any, *, commit: bool) -> Any:
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def _price_to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _monitor_query(
    db: Session,
    *,
    platform: str,
    handle: str,
    monitor_type: str,
    merchant_id: int | None,
):
    q = db.query(ProductSelectMonitor).filter(
        ProductSelectMonitor.platform == platform,
        ProductSelectMonitor.handle == handle,
        ProductSelectMonitor.monitor_type == monitor_type,
    )
    if merchant_id is None:
        q = q.filter(ProductSelectMonitor.merchant_id.is_(None))
    else:
        q = q.filter(ProductSelectMonitor.merchant_id == merchant_id)
    return q


def upsert_monitor(
    db: Session,
    *,
    platform: str,
    handle: str,
    monitor_type: str = "profile",
    merchant_id: int | None = None,
    display_name: str | None = None,
    score: float | Decimal = 5,
    is_enabled: bool = True,
    last_checked_at: datetime | None = None,
    commit: bool = True,
) -> ProductSelectMonitor:
    """按 (merchant_id, platform, handle, monitor_type) 更新或创建监控对象。

    MySQL UNIQUE KEY 中 merchant_id 允许 NULL，NULL 不会互相判重，所以这里显式查询后 upsert。
    """
    platform = platform.strip().lower()
    handle = handle.strip()
    monitor_type = monitor_type.strip().lower()
    row = _monitor_query(
        db,
        platform=platform,
        handle=handle,
        monitor_type=monitor_type,
        merchant_id=merchant_id,
    ).first()
    if row is None:
        row = ProductSelectMonitor(
            merchant_id=merchant_id,
            platform=platform,
            handle=handle,
            monitor_type=monitor_type,
        )
        db.add(row)

    row.display_name = display_name
    row.score = Decimal(str(score))
    row.is_enabled = is_enabled
    if last_checked_at is not None:
        row.last_checked_at = last_checked_at
    return _commit_refresh(db, row, commit=commit)


def list_monitors(
    db: Session,
    *,
    merchant_id: int | None = None,
    platform: str | None = None,
    is_enabled: bool | None = None,
    limit: int = 100,
) -> list[ProductSelectMonitor]:
    q = db.query(ProductSelectMonitor)
    if merchant_id is None:
        q = q.filter(ProductSelectMonitor.merchant_id.is_(None))
    else:
        q = q.filter(ProductSelectMonitor.merchant_id == merchant_id)
    if platform:
        q = q.filter(ProductSelectMonitor.platform == platform)
    if is_enabled is not None:
        q = q.filter(ProductSelectMonitor.is_enabled == is_enabled)
    return (
        q.order_by(ProductSelectMonitor.score.desc(), ProductSelectMonitor.id.asc())
        .limit(min(max(limit, 1), 500))
        .all()
    )


def get_monitor(
    db: Session,
    monitor_id: int,
    *,
    merchant_id: int | None = None,
) -> ProductSelectMonitor | None:
    q = db.query(ProductSelectMonitor).filter(ProductSelectMonitor.id == monitor_id)
    if merchant_id is None:
        q = q.filter(ProductSelectMonitor.merchant_id.is_(None))
    else:
        q = q.filter(ProductSelectMonitor.merchant_id == merchant_id)
    return q.first()


def update_monitor(
    db: Session,
    monitor_id: int,
    *,
    merchant_id: int | None = None,
    display_name: str | None = None,
    score: float | Decimal | None = None,
    is_enabled: bool | None = None,
    commit: bool = True,
) -> ProductSelectMonitor | None:
    row = get_monitor(db, monitor_id, merchant_id=merchant_id)
    if row is None:
        return None
    if display_name is not None:
        row.display_name = display_name
    if score is not None:
        row.score = Decimal(str(score))
    if is_enabled is not None:
        row.is_enabled = is_enabled
    return _commit_refresh(db, row, commit=commit)


def disable_monitor(
    db: Session,
    monitor_id: int,
    *,
    merchant_id: int | None = None,
    commit: bool = True,
) -> ProductSelectMonitor | None:
    return update_monitor(
        db,
        monitor_id,
        merchant_id=merchant_id,
        is_enabled=False,
        commit=commit,
    )


def upsert_content(
    db: Session,
    *,
    platform: str,
    external_id: str,
    monitor_id: int | None = None,
    merchant_id: int | None = None,
    url: str | None = None,
    caption_or_title: str | None = None,
    published_at: datetime | None = None,
    raw_path: str | None = None,
    status: str = "fetched",
    commit: bool = True,
) -> ProductSelectContent:
    """按 (platform, external_id) 更新或创建采集内容。"""
    platform = platform.strip().lower()
    external_id = str(external_id).strip()
    row = (
        db.query(ProductSelectContent)
        .filter(
            ProductSelectContent.platform == platform,
            ProductSelectContent.external_id == external_id,
        )
        .first()
    )
    if row is None:
        row = ProductSelectContent(platform=platform, external_id=external_id)
        db.add(row)

    row.monitor_id = monitor_id
    row.merchant_id = merchant_id
    row.url = url
    row.caption_or_title = caption_or_title
    row.published_at = published_at
    row.raw_path = raw_path
    row.status = status
    return _commit_refresh(db, row, commit=commit)


def get_content(
    db: Session,
    content_id: int,
) -> ProductSelectContent | None:
    return db.query(ProductSelectContent).filter(ProductSelectContent.id == content_id).first()


def create_image(
    db: Session,
    *,
    image_type: str,
    content_id: int | None = None,
    local_path: str | None = None,
    oss_key: str | None = None,
    oss_url: str | None = None,
    source_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    commit: bool = True,
) -> ProductSelectImage:
    row = ProductSelectImage(
        content_id=content_id,
        image_type=image_type,
        local_path=local_path,
        oss_key=oss_key,
        oss_url=oss_url,
        source_url=source_url,
        width=width,
        height=height,
    )
    db.add(row)
    return _commit_refresh(db, row, commit=commit)


def create_object(
    db: Session,
    *,
    category: str,
    content_id: int | None = None,
    source_image_id: int | None = None,
    crop_image_id: int | None = None,
    related_ip: str | None = None,
    description: str | None = None,
    attributes: list[str] | None = None,
    ecommerce_potential: str = "medium",
    reason: str | None = None,
    bbox: list[float] | None = None,
    recognition_version: int = 1,
    is_active: bool = True,
    token_usage: dict[str, Any] | None = None,
    commit: bool = True,
) -> ProductSelectObject:
    row = ProductSelectObject(
        content_id=content_id,
        source_image_id=source_image_id,
        crop_image_id=crop_image_id,
        category=category,
        related_ip=related_ip,
        description=description,
        attributes_json=attributes,
        ecommerce_potential=(ecommerce_potential or "medium").strip().lower(),
        reason=reason,
        bbox_json=bbox,
        recognition_version=recognition_version,
        is_active=is_active,
        token_usage_json=token_usage,
    )
    db.add(row)
    return _commit_refresh(db, row, commit=commit)


def next_object_version_for_content(db: Session, content_id: int) -> int:
    max_version = (
        db.query(ProductSelectObject.recognition_version)
        .filter(ProductSelectObject.content_id == content_id)
        .order_by(ProductSelectObject.recognition_version.desc())
        .limit(1)
        .scalar()
    )
    return int(max_version or 0) + 1


def deactivate_objects_for_content(
    db: Session,
    content_id: int,
    *,
    commit: bool = True,
) -> int:
    count = (
        db.query(ProductSelectObject)
        .filter(
            ProductSelectObject.content_id == content_id,
            ProductSelectObject.is_active.is_(True),
        )
        .update({ProductSelectObject.is_active: False}, synchronize_session=False)
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return count


def create_match(
    db: Session,
    *,
    object_id: int,
    source: str,
    title: str | None = None,
    store: str | None = None,
    url: str | None = None,
    price: float | Decimal | None = None,
    currency: str | None = None,
    rating: float | Decimal | None = None,
    reviews: int | None = None,
    in_stock: bool | None = None,
    thumbnail_url: str | None = None,
    match_level: str | None = None,
    raw_json: dict[str, Any] | None = None,
    commit: bool = True,
) -> ProductSelectMatch:
    row = ProductSelectMatch(
        object_id=object_id,
        source=source,
        match_level=match_level,
        title=title,
        store=store,
        url=url,
        price=_price_to_decimal(price),
        currency=currency,
        rating=_price_to_decimal(rating),
        reviews=reviews,
        in_stock=in_stock,
        thumbnail_url=thumbnail_url,
        raw_json=raw_json,
    )
    db.add(row)
    return _commit_refresh(db, row, commit=commit)


def create_matches_from_lens(
    db: Session,
    *,
    object_id: int,
    lens_response: dict[str, Any],
    commit: bool = True,
) -> list[ProductSelectMatch]:
    """把 SerpApi Google Lens 的 visual_matches 展开写入 product_select_matches。"""
    rows: list[ProductSelectMatch] = []
    for item in lens_response.get("visual_matches") or []:
        if not isinstance(item, dict):
            continue
        price_info = item.get("price") if isinstance(item.get("price"), dict) else {}
        row = ProductSelectMatch(
            object_id=object_id,
            source="google_lens",
            title=item.get("title"),
            store=item.get("source"),
            url=item.get("link"),
            price=_price_to_decimal(price_info.get("extracted_value")),
            currency=price_info.get("currency"),
            rating=_price_to_decimal(item.get("rating")),
            reviews=int(item["reviews"]) if isinstance(item.get("reviews"), (int, float)) else None,
            in_stock=item.get("in_stock") if isinstance(item.get("in_stock"), bool) else None,
            thumbnail_url=item.get("thumbnail"),
            raw_json=item,
        )
        db.add(row)
        rows.append(row)
    if commit:
        db.commit()
        for row in rows:
            db.refresh(row)
    else:
        db.flush()
    return rows


def get_object(
    db: Session,
    object_id: int,
) -> ProductSelectObject | None:
    return db.query(ProductSelectObject).filter(ProductSelectObject.id == object_id).first()


def get_image(
    db: Session,
    image_id: int,
) -> ProductSelectImage | None:
    return db.query(ProductSelectImage).filter(ProductSelectImage.id == image_id).first()


def delete_matches_for_object(
    db: Session,
    object_id: int,
    *,
    source: str | None = None,
    commit: bool = True,
) -> int:
    q = db.query(ProductSelectMatch).filter(ProductSelectMatch.object_id == object_id)
    if source:
        q = q.filter(ProductSelectMatch.source == source)
    count = q.delete(synchronize_session=False)
    if commit:
        db.commit()
    else:
        db.flush()
    return count


def list_objects(
    db: Session,
    *,
    potential: str | None = None,
    related_ip: str | None = None,
    category: str | None = None,
    include_test: bool = False,
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[ProductSelectObject]:
    q = db.query(ProductSelectObject)
    if not include_test:
        q = q.filter(ProductSelectObject.content_id.is_not(None))
    if active_only:
        q = q.filter(ProductSelectObject.is_active.is_(True))
    if potential:
        q = q.filter(ProductSelectObject.ecommerce_potential == potential.strip().lower())
    if related_ip:
        q = q.filter(ProductSelectObject.related_ip == related_ip)
    if category:
        q = q.filter(ProductSelectObject.category == category)
    return (
        q.order_by(ProductSelectObject.id.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 500))
        .all()
    )


def list_matches(
    db: Session,
    *,
    object_id: int,
    source: str | None = None,
    limit: int = 100,
) -> list[ProductSelectMatch]:
    q = db.query(ProductSelectMatch).filter(ProductSelectMatch.object_id == object_id)
    if source:
        q = q.filter(ProductSelectMatch.source == source)
    return (
        q.order_by(ProductSelectMatch.id.asc())
        .limit(min(max(limit, 1), 500))
        .all()
    )


def get_object_profile(
    db: Session,
    profile_id: int,
) -> ProductSelectObjectProfile | None:
    return db.query(ProductSelectObjectProfile).filter(ProductSelectObjectProfile.id == profile_id).first()


def get_active_object_profile(
    db: Session,
    object_id: int,
) -> ProductSelectObjectProfile | None:
    return (
        db.query(ProductSelectObjectProfile)
        .filter(
            ProductSelectObjectProfile.object_id == object_id,
            ProductSelectObjectProfile.is_active.is_(True),
        )
        .order_by(ProductSelectObjectProfile.id.desc())
        .first()
    )


def deactivate_profiles_for_object(
    db: Session,
    object_id: int,
    *,
    commit: bool = True,
) -> int:
    count = (
        db.query(ProductSelectObjectProfile)
        .filter(
            ProductSelectObjectProfile.object_id == object_id,
            ProductSelectObjectProfile.is_active.is_(True),
        )
        .update({ProductSelectObjectProfile.is_active: False}, synchronize_session=False)
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return count


def create_object_profile(
    db: Session,
    *,
    object_id: int,
    cost_price_min: float | Decimal | None = None,
    cost_price_max: float | Decimal | None = None,
    selling_price_min: float | Decimal | None = None,
    selling_price_max: float | Decimal | None = None,
    currency: str | None = "USD",
    length_cm: float | Decimal | None = None,
    width_cm: float | Decimal | None = None,
    height_cm: float | Decimal | None = None,
    volume_cm3: float | Decimal | None = None,
    weight_value: float | Decimal | None = None,
    weight_unit: str | None = None,
    source: str = "ai",
    status: str = "draft",
    reference_match_id: int | None = None,
    notes: str | None = None,
    confidence_score: float | Decimal | None = None,
    estimate_detail_json: dict[str, Any] | None = None,
    is_active: bool = True,
    deactivate_existing: bool = True,
    commit: bool = True,
) -> ProductSelectObjectProfile:
    if deactivate_existing and is_active:
        deactivate_profiles_for_object(db, object_id, commit=False)
    row = ProductSelectObjectProfile(
        object_id=object_id,
        cost_price_min=_price_to_decimal(cost_price_min),
        cost_price_max=_price_to_decimal(cost_price_max),
        selling_price_min=_price_to_decimal(selling_price_min),
        selling_price_max=_price_to_decimal(selling_price_max),
        currency=(currency or "USD").strip().upper() or "USD",
        length_cm=_price_to_decimal(length_cm),
        width_cm=_price_to_decimal(width_cm),
        height_cm=_price_to_decimal(height_cm),
        volume_cm3=_price_to_decimal(volume_cm3),
        weight_value=_price_to_decimal(weight_value),
        weight_unit=(weight_unit or "").strip().lower() or None,
        source=(source or "ai").strip().lower(),
        status=(status or "draft").strip().lower(),
        reference_match_id=reference_match_id,
        notes=notes,
        confidence_score=_price_to_decimal(confidence_score),
        estimate_detail_json=estimate_detail_json,
        is_active=is_active,
    )
    db.add(row)
    return _commit_refresh(db, row, commit=commit)


def update_object_profile(
    db: Session,
    profile_id: int,
    *,
    cost_price_min: float | Decimal | None = None,
    cost_price_max: float | Decimal | None = None,
    selling_price_min: float | Decimal | None = None,
    selling_price_max: float | Decimal | None = None,
    currency: str | None = None,
    length_cm: float | Decimal | None = None,
    width_cm: float | Decimal | None = None,
    height_cm: float | Decimal | None = None,
    volume_cm3: float | Decimal | None = None,
    weight_value: float | Decimal | None = None,
    weight_unit: str | None = None,
    source: str | None = None,
    status: str | None = None,
    reference_match_id: int | None = None,
    notes: str | None = None,
    confidence_score: float | Decimal | None = None,
    estimate_detail_json: dict[str, Any] | None = None,
    is_active: bool | None = None,
    commit: bool = True,
) -> ProductSelectObjectProfile | None:
    row = get_object_profile(db, profile_id)
    if row is None:
        return None

    updates = {
        "cost_price_min": cost_price_min,
        "cost_price_max": cost_price_max,
        "selling_price_min": selling_price_min,
        "selling_price_max": selling_price_max,
        "currency": currency,
        "length_cm": length_cm,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "volume_cm3": volume_cm3,
        "weight_value": weight_value,
        "weight_unit": weight_unit,
        "source": source,
        "status": status,
        "reference_match_id": reference_match_id,
        "notes": notes,
        "confidence_score": confidence_score,
        "estimate_detail_json": estimate_detail_json,
        "is_active": is_active,
    }
    for field, value in updates.items():
        if value is None:
            continue
        if field in {
            "cost_price_min",
            "cost_price_max",
            "selling_price_min",
            "selling_price_max",
            "length_cm",
            "width_cm",
            "height_cm",
            "volume_cm3",
            "weight_value",
            "confidence_score",
        }:
            setattr(row, field, _price_to_decimal(value))
        elif field == "currency":
            row.currency = str(value).strip().upper() or row.currency
        elif field in {"source", "status"}:
            setattr(row, field, str(value).strip().lower())
        elif field == "weight_unit":
            row.weight_unit = str(value).strip().lower() or None
        else:
            setattr(row, field, value)

    if is_active is True and row.object_id:
        deactivate_profiles_for_object(db, row.object_id, commit=False)
        row.is_active = True

    return _commit_refresh(db, row, commit=commit)


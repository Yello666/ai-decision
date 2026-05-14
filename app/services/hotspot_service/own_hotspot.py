"""商户自上传热点（hotspots 表）的 CRUD + 推荐：与 YouTube 全局热点缓存解耦，按 merchant_id 隔离。

推荐逻辑只换数据源（MySQL ``hotspots`` 表 vs Redis 全量缓存），匹配模型、缓存、过滤
完全复用 ``batch_match_hotspot_for_brand_async`` 的现有实现。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Hotspot
from app.schemas.hotspot import (
    BrandObject,
    CollectTrendObject,
    HotspotMatchResponse,
    ProductOpportunity,
    TrendObject,
)
from app.schemas.own_hotspot import (
    OwnHotspotCreate,
    OwnHotspotOut,
    OwnHotspotRecommendedItem,
    OwnHotspotUpdate,
)
from app.services.hotspot_service.analyse_matching_degree import (
    batch_match_hotspot_for_brand_async,
)
from app.services.hotspot_service.collect_hostspot import analyze_collect_trend_items_async

logger = logging.getLogger(__name__)

# 单次推荐最多参与匹配的用户热点条数，防止某商户上传过多导致 LLM 调用爆炸
_MAX_OWN_HOTSPOTS_PER_RECOMMEND = 100


def _csv_to_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s and s.strip()]


def _list_to_csv(items: Optional[List[str]]) -> Optional[str]:
    if not items:
        return None
    cleaned = [s.strip() for s in items if s and s.strip()]
    return ",".join(cleaned) if cleaned else None


def _row_to_out(row: Hotspot) -> OwnHotspotOut:
    return OwnHotspotOut(
        id=row.id,
        merchant_id=row.merchant_id,
        title=row.title,
        summary=row.summary,
        tags=_csv_to_list(row.tags),
        audience=_csv_to_list(row.audience),
        created_at=row.created_at,
    )


def _get_row(db: Session, merchant_id: int, hotspot_id: int) -> Optional[Hotspot]:
    return (
        db.query(Hotspot)
        .filter(Hotspot.merchant_id == merchant_id, Hotspot.id == hotspot_id)
        .first()
    )


def list_for_merchant(
    db: Session,
    merchant_id: int,
    *,
    limit: int = 50,
) -> List[OwnHotspotOut]:
    cap = min(max(limit, 1), 250)
    rows = (
        db.query(Hotspot)
        .filter(Hotspot.merchant_id == merchant_id)
        .order_by(Hotspot.id.desc())
        .limit(cap)
        .all()
    )
    return [_row_to_out(r) for r in rows]


def get_for_merchant(db: Session, merchant_id: int, hotspot_id: int) -> Optional[OwnHotspotOut]:
    row = _get_row(db, merchant_id, hotspot_id)
    return _row_to_out(row) if row else None


def create_for_merchant(
    db: Session,
    merchant_id: int,
    body: OwnHotspotCreate,
) -> OwnHotspotOut:
    row = Hotspot(
        merchant_id=merchant_id,
        title=body.title.strip(),
        summary=body.summary.strip(),
        tags=_list_to_csv(body.tags),
        audience=_list_to_csv(body.audience),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


def update_for_merchant(
    db: Session,
    merchant_id: int,
    hotspot_id: int,
    body: OwnHotspotUpdate,
) -> Optional[OwnHotspotOut]:
    row = _get_row(db, merchant_id, hotspot_id)
    if not row:
        return None

    patch = body.model_dump(exclude_unset=True)
    if "title" in patch and patch["title"] is not None:
        row.title = patch["title"].strip()
    if "summary" in patch and patch["summary"] is not None:
        row.summary = patch["summary"].strip()
    if "tags" in patch:
        row.tags = _list_to_csv(patch["tags"])
    if "audience" in patch:
        row.audience = _list_to_csv(patch["audience"])

    db.commit()
    db.refresh(row)
    return _row_to_out(row)


def delete_for_merchant(db: Session, merchant_id: int, hotspot_id: int) -> bool:
    row = _get_row(db, merchant_id, hotspot_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


# --- 推荐：拉全量 → 调匹配 → 按契合度过滤 -------------------------------------------


def _own_to_trend_object(item: OwnHotspotOut) -> TrendObject:
    return TrendObject(
        title=item.title,
        summary=item.summary,
        tags=list(item.tags or []),
        audience=list(item.audience) if item.audience else None,
        product_opportunities=list(item.product_opportunities or []),
    )


def _own_to_collect_trend_object(item: OwnHotspotOut) -> CollectTrendObject:
    return CollectTrendObject(
        id=f"own-{item.id}",
        title=item.title,
        summary=item.summary,
        tags=list(item.tags or []),
        audience=list(item.audience or []),
        jump_url="",
        view_count=0,
        likes=0,
        publish_time=item.created_at.isoformat() if item.created_at else "",
        platform="own-hotspot",
    )


async def _with_product_opportunities(
    candidates: List[OwnHotspotOut],
) -> List[OwnHotspotOut]:
    """Generate recommendation-only product opportunities without persisting them."""
    if not candidates:
        return []

    collect_items = [_own_to_collect_trend_object(c) for c in candidates]
    analyzed_items = await analyze_collect_trend_items_async(collect_items)
    analyzed_by_id = {item.id: item for item in analyzed_items}

    enriched: List[OwnHotspotOut] = []
    for candidate in candidates:
        analyzed = analyzed_by_id.get(f"own-{candidate.id}")
        raw_opportunities = analyzed.product_opportunities if analyzed else []
        product_opportunities = [
            ProductOpportunity.model_validate(opportunity)
            for opportunity in (raw_opportunities or [])
        ]
        enriched.append(
            candidate.model_copy(
                update={"product_opportunities": product_opportunities}
            )
        )
    return enriched


async def build_own_recommended_hotspots(
    *,
    db: Session,
    merchant_id: int,
    brand: BrandObject,
    min_compatibility_score: float,
) -> tuple[List[OwnHotspotRecommendedItem], int]:
    """
    返回 (筛选后的推荐列表, 实际参与匹配的分析条数)。

    分析条数 = 当前商户在 hotspots 表中的全部条目数（受 _MAX_OWN_HOTSPOTS_PER_RECOMMEND 上限）。
    """
    candidates: List[OwnHotspotOut] = list_for_merchant(
        db,
        merchant_id,
        limit=_MAX_OWN_HOTSPOTS_PER_RECOMMEND,
    )
    analyzed_count = len(candidates)
    if not candidates:
        return [], 0

    candidates = await _with_product_opportunities(candidates)
    trends = [_own_to_trend_object(c) for c in candidates]
    matches: List[HotspotMatchResponse] = await batch_match_hotspot_for_brand_async(
        trends=trends,
        brand=brand,
    )

    if len(matches) != len(candidates):
        logger.error(
            "用户热点匹配结果条数与候选不一致 candidates=%d matches=%d",
            len(candidates),
            len(matches),
        )
        raise RuntimeError("用户热点匹配结果条数异常")

    items: List[OwnHotspotRecommendedItem] = []
    for hotspot_obj, match_obj in zip(candidates, matches):
        if match_obj.compatibility_score >= min_compatibility_score:
            items.append(
                OwnHotspotRecommendedItem(hotspot=hotspot_obj, match=match_obj)
            )

    return items, analyzed_count

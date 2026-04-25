"""
组合热点全量缓存与品牌匹配：对缓存中全部热点做匹配，再按契合度下限筛选。
"""
from __future__ import annotations

import logging
from typing import List

from app.core.hot_trends_cache import get_hot_trends_cached
from app.schemas.hotspot import (
    BrandObject,
    CollectTrendObject,
    HotspotLLMModel,
    HotspotMatchResponse,
    HotspotRecommendedItem,
    TrendObject,
)
from app.services.hotspot_service.analyse_matching_degree import (
    batch_match_hotspot_for_brand_async,
)
from app.services.hotspot_service.collect_hostspot import collect_and_format_hot_data_async

logger = logging.getLogger(__name__)


def _collect_to_trend_object(item: CollectTrendObject) -> TrendObject:
    return TrendObject(
        title=item.title,
        summary=item.summary,
        tags=list(item.tags or []),
        audience=item.audience,
    )


async def build_recommended_hotspots(
    *,
    platforms: List[str],
    min_compatibility_score: float,
    brand: BrandObject,
    llm_model: HotspotLLMModel,
) -> tuple[List[HotspotRecommendedItem], int]:
    """
    返回 (筛选后的推荐列表, 实际参与匹配的分析条数)。

    分析条数 = 全量缓存中的热点条数（与 /hot-trends 同源，最多 50 条）。
    """
    all_trends = await get_hot_trends_cached(
        platforms,
        loader=collect_and_format_hot_data_async,
    )
    candidates: List[CollectTrendObject] = list(all_trends)
    analyzed_count = len(candidates)
    if not candidates:
        return [], 0

    trends = [_collect_to_trend_object(t) for t in candidates]
    matches: List[HotspotMatchResponse] = await batch_match_hotspot_for_brand_async(
        trends=trends,
        brand=brand,
        llm_model=llm_model,
    )

    if len(matches) != len(candidates):
        logger.error(
            "匹配结果条数与候选不一致 candidates=%d matches=%d",
            len(candidates),
            len(matches),
        )
        raise RuntimeError("热点匹配结果条数异常")

    items: List[HotspotRecommendedItem] = []
    for trend_obj, match_obj in zip(candidates, matches):
        if match_obj.compatibility_score >= min_compatibility_score:
            items.append(HotspotRecommendedItem(trend=trend_obj, match=match_obj))

    return items, analyzed_count

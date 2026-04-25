"""
(品牌, 热点) 匹配度缓存。

目的：同一个品牌对同一个热点只分析一次，后续再次出现直接复用历史匹配结果。

Key 设计：
    hotspot:match:{version}:{brand_fp}:{trend_fp}

- version：模型/Prompt 升级时切换即可整体失效
- brand_fp：品牌核心字段的短 hash
- trend_fp：热点核心字段的短 hash（title+summary+tags+audience）
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Tuple

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.schemas.hotspot import BrandObject, TrendObject

logger = logging.getLogger(__name__)

_MATCH_PREFIX = "hotspot:match"


def _short_hash(raw: str) -> str:
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def brand_fingerprint(brand: BrandObject) -> str:
    raw = json.dumps(
        {
            "name": (brand.name or "").strip(),
            "mainly_sold_products": (brand.mainly_sold_products or "").strip(),
            "core_value": (brand.core_value or "").strip(),
            "tone": (brand.tone or "").strip(),
            "audience": sorted([a.strip() for a in (brand.audience or []) if a and a.strip()]),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _short_hash(raw)


def trend_fingerprint(trend: TrendObject) -> str:
    raw = json.dumps(
        {
            "title": (trend.title or "").strip(),
            "summary": (trend.summary or "").strip(),
            "tags": sorted([t.strip() for t in (trend.tags or []) if t and t.strip()]),
            "audience": sorted([a.strip() for a in (trend.audience or []) if a and a.strip()]),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _short_hash(raw)


def build_match_key(brand_fp: str, trend: TrendObject) -> str:
    settings = get_settings()
    return (
        f"{_MATCH_PREFIX}:{settings.HOT_TRENDS_MATCH_VERSION}:"
        f"{brand_fp}:{trend_fingerprint(trend)}"
    )


async def mget_match(
    brand: BrandObject,
    trends: List[TrendObject],
) -> Tuple[str, Dict[int, Dict[str, Any]], List[int]]:
    """
    批量读取匹配缓存。返回 (brand_fp, 命中 map[trend_index->result], 未命中 index 列表)。
    使用 trend 在输入列表中的索引作为定位，避免 title 重复导致冲突。
    """
    brand_fp = brand_fingerprint(brand)
    if not trends:
        return brand_fp, {}, []

    redis = get_redis_client()
    keys = [build_match_key(brand_fp, t) for t in trends]

    try:
        raw_values = await redis.mget(keys)
    except Exception:
        logger.exception("匹配缓存 MGET 失败，全部按未命中处理")
        return brand_fp, {}, list(range(len(trends)))

    hit_map: Dict[int, Dict[str, Any]] = {}
    miss_indices: List[int] = []
    for idx, raw in enumerate(raw_values):
        if not raw:
            miss_indices.append(idx)
            continue
        try:
            hit_map[idx] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("匹配缓存反序列化失败, idx=%d，重新分析", idx)
            miss_indices.append(idx)
    return brand_fp, hit_map, miss_indices


async def set_match_many(
    brand_fp: str,
    trends: List[TrendObject],
    results: Dict[int, Dict[str, Any]],
) -> None:
    """批量写入匹配缓存。results: {trend_index: match_result_dict}。"""
    if not trends or not results:
        return

    settings = get_settings()
    redis = get_redis_client()
    ttl = settings.HOT_TRENDS_MATCH_CACHE_TTL_SECONDS

    try:
        pipe = redis.pipeline(transaction=False)
        for idx, result in results.items():
            if idx < 0 or idx >= len(trends) or not result:
                continue
            key = build_match_key(brand_fp, trends[idx])
            pipe.set(key, json.dumps(result, ensure_ascii=False), ex=ttl)
        await pipe.execute()
    except Exception:
        logger.exception("匹配缓存批量写入失败")

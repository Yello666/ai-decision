"""
单热点 LLM 分析结果缓存。

目的：同一个热点（同平台 + 同 id + 同内容指纹）只交给大模型分析一次，
后续再次出现时直接复用历史分析结果，避免重复消耗 token。

Key 设计：
    hotspot:analysis:{version}:{platform}:{id}:{fingerprint}

- version：来自 settings.HOT_TRENDS_ANALYSIS_VERSION，模型/Prompt 升级时切版本即可整体失效
- platform：避免不同平台同 id 冲突
- id：原始热点 id（YouTube videoId 等）
- fingerprint：title+summary+tags 的短 hash，内容变更时自动绕过旧缓存
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.schemas.hotspot import CollectTrendObject

logger = logging.getLogger(__name__)

_ANALYSIS_PREFIX = "hotspot:analysis"


def _content_fingerprint(item: CollectTrendObject) -> str:
    raw = json.dumps(
        {
            "title": (item.title or "").strip(),
            "summary": (item.summary or "").strip(),
            "tags": sorted([t.strip() for t in (item.tags or []) if t and t.strip()]),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_analysis_key(item: CollectTrendObject) -> str:
    settings = get_settings()
    platform = (item.platform or "unknown").strip().lower()
    return (
        f"{_ANALYSIS_PREFIX}:{settings.HOT_TRENDS_ANALYSIS_VERSION}:"
        f"{platform}:{item.id}:{_content_fingerprint(item)}"
    )


async def mget_analysis(
    items: List[CollectTrendObject],
) -> Tuple[Dict[str, Dict[str, Any]], List[CollectTrendObject]]:
    """批量读取分析缓存。返回 (命中结果 map[id->analysis], 未命中 item 列表)。"""
    if not items:
        return {}, []

    redis = get_redis_client()
    keys = [build_analysis_key(i) for i in items]

    try:
        raw_values = await redis.mget(keys)
    except Exception:
        logger.exception("分析缓存 MGET 失败，全部按未命中处理")
        return {}, list(items)

    hit_map: Dict[str, Dict[str, Any]] = {}
    miss: List[CollectTrendObject] = []
    for item, raw in zip(items, raw_values):
        if not raw:
            miss.append(item)
            continue
        try:
            hit_map[item.id] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("分析缓存反序列化失败, id=%s，重新分析", item.id)
            miss.append(item)
    return hit_map, miss


async def set_analysis_many(
    items: List[CollectTrendObject],
    analyses: Dict[str, Dict[str, Any]],
) -> None:
    """批量写入分析缓存。analyses: {item.id: analysis_dict}。"""
    if not items or not analyses:
        return

    settings = get_settings()
    redis = get_redis_client()
    ttl = settings.HOT_TRENDS_ANALYSIS_CACHE_TTL_SECONDS

    try:
        pipe = redis.pipeline(transaction=False)
        for item in items:
            analysis = analyses.get(item.id)
            if not analysis:
                continue
            await pipe.set(build_analysis_key(item), json.dumps(analysis, ensure_ascii=False), ex=ttl)
        await pipe.execute()
    except Exception:
        logger.exception("分析缓存批量写入失败")


async def set_analysis(item: CollectTrendObject, analysis: Optional[Dict[str, Any]]) -> None:
    if not analysis:
        return
    await set_analysis_many([item], {item.id: analysis})

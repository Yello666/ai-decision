"""
热点「推荐」参数（min_compatibility_score）在 Redis 中的持久化。

按商户隔离；请求到达时与缓存对比，不一致则覆盖写入。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)

_PREFS_KEY_PREFIX = "hotspot:recommend:prefs"


def _prefs_key(merchant_id: int) -> str:
    return f"{_PREFS_KEY_PREFIX}:{merchant_id}"


async def get_recommend_prefs(merchant_id: int) -> Optional[float]:
    """读取商户上次保存的最低契合度；不存在则返回 None。"""
    redis = get_redis_client()
    key = _prefs_key(merchant_id)
    try:
        raw = await redis.get(key)
    except Exception:
        logger.exception("读取热点推荐偏好失败 merchant_id=%s", merchant_id)
        return None
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return float(obj["min_compatibility_score"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("热点推荐偏好反序列化失败 merchant_id=%s", merchant_id)
        return None


async def sync_recommend_prefs(
    merchant_id: int,
    min_compatibility_score: float,
) -> None:
    """
    若与 Redis 中已存参数不一致则覆盖写入；首次写入也会执行。
    """
    redis = get_redis_client()
    key = _prefs_key(merchant_id)
    stored = await get_recommend_prefs(merchant_id)
    if stored is not None and abs(stored - min_compatibility_score) < 1e-9:
        return

    payload = json.dumps(
        {
            "min_compatibility_score": min_compatibility_score,
        },
        ensure_ascii=False,
    )
    try:
        await redis.set(key, payload)
        logger.info(
            "已更新热点推荐偏好 merchant_id=%s min_compatibility_score=%s",
            merchant_id,
            min_compatibility_score,
        )
    except Exception:
        logger.exception("写入热点推荐偏好失败 merchant_id=%s", merchant_id)

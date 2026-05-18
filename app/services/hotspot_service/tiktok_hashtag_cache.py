from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.schemas.hotspot import CollectTrendObject, TikTokHashtagTrendRequest

logger = logging.getLogger(__name__)

_PREFIX = "tiktok:hashtag:analyzed"
_LOCK_SUFFIX = ":lock"
_LOCK_TTL_SECONDS = 60
_SPIN_INTERVAL_SECONDS = 0.5
_SPIN_MAX_WAIT_SECONDS = 10.0


# 归一化 hashtag 列表，保证顺序不同但集合相同的请求命中同一缓存。
def _normalize_hashtags(values: list[str]) -> list[str]:
    normalized = {
        str(tag).strip().lstrip("#").lower()
        for tag in values or []
        if str(tag).strip().lstrip("#")
    }
    return sorted(normalized)


# 根据完整抓取参数生成稳定指纹，避免 Redis key 过长。
def _cache_fingerprint(request: TikTokHashtagTrendRequest) -> str:
    payload: dict[str, Any] = {
        "hashtags": _normalize_hashtags(request.hashtags),
        "max_results": request.max_results,
        "comments_per_post": request.comments_per_post,
        "max_replies_per_comment": request.max_replies_per_comment,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# 拼出 TikTok hashtag 分析结果缓存的 Redis key。
def _cache_key(request: TikTokHashtagTrendRequest) -> str:
    settings = get_settings()
    return f"{_PREFIX}:{settings.TIKTOK_HASHTAG_TRENDS_CACHE_VERSION}:{_cache_fingerprint(request)}"


# 将分析后的热点列表序列化为 Redis 可存储的 JSON 字符串。
def _serialize(items: list[CollectTrendObject]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        ensure_ascii=False,
    )


# 将 Redis 中的 JSON 字符串还原为 CollectTrendObject 列表。
def _deserialize(raw: str) -> list[CollectTrendObject]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("TikTok hashtag 缓存内容不是列表")
    return [CollectTrendObject.model_validate(item) for item in data]


# 从 Redis 读取缓存，读取或解析失败时返回 None 以便回源。
async def _read_cached(redis, key: str) -> list[CollectTrendObject] | None:
    try:
        raw = await redis.get(key)
    except Exception:
        logger.exception("TikTok hashtag 缓存读取失败, key=%s", key)
        return None
    if not raw:
        return None
    try:
        return _deserialize(raw)
    except Exception:
        logger.warning("TikTok hashtag 缓存反序列化失败, key=%s", key, exc_info=True)
        return None


# 将分析结果写入 Redis，并使用配置中的 TTL 控制有效期。
async def _write_cached(redis, key: str, items: list[CollectTrendObject]) -> None:
    ttl = get_settings().TIKTOK_HASHTAG_TRENDS_CACHE_TTL_SECONDS
    try:
        await redis.setex(key, ttl, _serialize(items))
    except Exception:
        logger.exception("TikTok hashtag 缓存写入失败, key=%s", key)


# 获取 TikTok hashtag 分析结果：优先读缓存，未命中时加锁回源。
async def get_tiktok_hashtag_analyzed_cached(
    request: TikTokHashtagTrendRequest,
    loader: Callable[[], Awaitable[list[CollectTrendObject]]],
) -> list[CollectTrendObject]:
    """按完整抓取参数缓存 TikTok hashtag 分析结果；排序参数不参与缓存 key。"""
    key = _cache_key(request)
    lock_key = key + _LOCK_SUFFIX
    redis = get_redis_client()

    cached = await _read_cached(redis, key)
    if cached is not None:
        logger.info("TikTok hashtag 分析缓存命中 key=%s 条目数=%d", key, len(cached))
        return cached

    logger.info("TikTok hashtag 分析缓存未命中 key=%s", key)
    acquired = False
    try:
        acquired = bool(await redis.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception:
        logger.exception("TikTok hashtag 缓存锁获取失败, lock_key=%s, 直接回源", lock_key)
        acquired = True

    if not acquired:
        deadline = asyncio.get_running_loop().time() + _SPIN_MAX_WAIT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_SPIN_INTERVAL_SECONDS)
            cached = await _read_cached(redis, key)
            if cached is not None:
                logger.info("TikTok hashtag 分析缓存等待命中 key=%s 条目数=%d", key, len(cached))
                return cached
        logger.warning("TikTok hashtag 分析缓存等待超时 key=%s, 当前请求回源", key)

    try:
        items = await loader()
        await _write_cached(redis, key, items)
        logger.info("TikTok hashtag 分析缓存回源成功 key=%s 条目数=%d", key, len(items))
        return items
    finally:
        if acquired:
            try:
                await redis.delete(lock_key)
            except Exception:
                logger.exception("TikTok hashtag 缓存锁释放失败, lock_key=%s", lock_key)

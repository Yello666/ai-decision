"""
热点接口逻辑过期缓存（生产级）

策略：
- 每个平台组合只维护一份"全量缓存"（固定拉 CACHE_MAX_RESULTS 条）
- 分页切片由调用方完成，缓存层不关心 page/page_size
- Redis key 不设物理 TTL，仅靠 JSON 内 expire_at 做逻辑过期判断
"""
import asyncio
import json
import logging
import time
from typing import List, Callable, Awaitable

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.schemas.hotspot import CollectTrendObject

logger = logging.getLogger(__name__)

HOT_TRENDS_PREFIX = "hot_trends:"
LOCK_SUFFIX = ":lock"

CACHE_MAX_RESULTS = 50
LOCK_TTL_SECONDS = 30
SPIN_INTERVAL = 0.3
SPIN_MAX_WAIT = LOCK_TTL_SECONDS
DEFAULT_PRELOAD_PLATFORMS = ["youtube"]

settings = get_settings()


def _normalize_platforms(platforms: List[str] | None) -> List[str]:
    """归一化平台列表：小写、去空白、去重、排序，避免同义 key 分裂。"""
    raw = platforms or ["youtube"]
    seen: set[str] = set()
    result: list[str] = []
    for p in raw:
        key = p.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return sorted(result) if result else ["youtube"]


def _cache_key(platforms: List[str]) -> str:
    return f"{HOT_TRENDS_PREFIX}{','.join(platforms)}"


def _serialize(data: List[CollectTrendObject]) -> str:
    return json.dumps(
        {
            "data": [x.model_dump(mode="json") for x in data],
            "expire_at": time.time() + settings.HOT_TRENDS_LOGICAL_TTL_SECONDS,
        },
        ensure_ascii=False,
    )


def _deserialize(raw: str) -> tuple[List[CollectTrendObject], float]:
    obj = json.loads(raw)
    data = [CollectTrendObject.model_validate(x) for x in obj["data"]]
    expire_at = float(obj.get("expire_at", 0))
    return data, expire_at


async def get_hot_trends_cached(
    platforms: List[str],
    loader: Callable[[List[str], int], Awaitable[List[CollectTrendObject]]],
) -> List[CollectTrendObject]:
    """
    返回指定平台组合的全量热点（最多 CACHE_MAX_RESULTS 条）。
    逻辑过期缓存：有数据且未过期直接返回；过期先返旧数据并单飞刷新；缺失则加锁回源。
    """
    norm_platforms = _normalize_platforms(platforms)
    key = _cache_key(norm_platforms)
    lock_key = key + LOCK_SUFFIX
    redis = get_redis_client()

    # ---------- 1. 尝试读缓存 ----------
    try:
        raw = await redis.get(key)
    except Exception:
        logger.exception("Redis GET 失败, key=%s, 降级回源", key)
        raw = None

    if raw:
        try:
            data, expire_at = _deserialize(raw)
        except (json.JSONDecodeError, KeyError):
            logger.warning("缓存反序列化失败, key=%s, 走回源", key)
            data = None
        else:
            if time.time() < expire_at:
                logger.debug("缓存命中 (未过期), key=%s", key)
                return data

            logger.info("缓存逻辑过期, key=%s, 返回旧数据并触发后台刷新", key)
            try:
                acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
            except Exception:
                logger.exception("Redis SET(lock) 失败, key=%s", lock_key)
                acquired = False
            if acquired:
                asyncio.create_task(
                    _refresh_and_set(redis, key, lock_key, norm_platforms, loader)
                )
            return data

    # ---------- 2. 缓存缺失：加锁回源 ----------
    logger.info("缓存未命中, key=%s, 加锁回源", key)
    deadline = time.monotonic() + SPIN_MAX_WAIT

    while True:
        try:
            acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
        except Exception:
            logger.exception("Redis SET(lock) 失败, key=%s, 直接回源", lock_key)
            acquired = True
            break
        if acquired:
            break

        if time.monotonic() > deadline:
            logger.warning("等待回源超时, key=%s, 直接回源", key)
            break
        await asyncio.sleep(SPIN_INTERVAL)

        try:
            raw = await redis.get(key)
        except Exception:
            logger.exception("Redis GET(spin) 失败, key=%s", key)
            raw = None
        if raw:
            try:
                data, _ = _deserialize(raw)
                return data
            except (json.JSONDecodeError, KeyError):
                logger.warning("自旋中反序列化失败, key=%s", key)

    try:
        data = await loader(norm_platforms, CACHE_MAX_RESULTS)
        await redis.set(key, _serialize(data))
        logger.info("回源成功并写入缓存, key=%s, 条目数=%d", key, len(data))
        return data
    except Exception:
        logger.exception("loader 回源失败, key=%s", key)
        raise
    finally:
        try:
            await redis.delete(lock_key)
        except Exception:
            logger.exception("释放锁失败, lock_key=%s", lock_key)


async def _refresh_and_set(
    redis,
    key: str,
    lock_key: str,
    platforms: List[str],
    loader: Callable[[List[str], int], Awaitable[List[CollectTrendObject]]],
) -> None:
    """后台单飞刷新：成功则更新缓存，失败只记日志、不影响已返回的旧数据。"""
    try:
        data = await loader(platforms, CACHE_MAX_RESULTS)
        await redis.set(key, _serialize(data))
        logger.info("后台刷新成功, key=%s, 条目数=%d", key, len(data))
    except Exception:
        logger.exception("后台刷新失败, key=%s", key)
    finally:
        try:
            await redis.delete(lock_key)
        except Exception:
            logger.exception("后台刷新释放锁失败, lock_key=%s", lock_key)


async def preload_hot_trends_cache(
    loader: Callable[[List[str], int], Awaitable[List[CollectTrendObject]]],
    platforms: List[str] | None = None,
) -> None:
    """启动预热缓存：强制回源并覆盖缓存，确保首个请求可直接命中。"""
    norm_platforms = _normalize_platforms(platforms or DEFAULT_PRELOAD_PLATFORMS)
    key = _cache_key(norm_platforms)
    redis = get_redis_client()

    data = await loader(norm_platforms, CACHE_MAX_RESULTS)
    await redis.set(key, _serialize(data))
    logger.info("启动预热热点缓存成功, key=%s, 条目数=%d", key, len(data))

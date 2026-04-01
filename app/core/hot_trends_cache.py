"""
热点接口逻辑过期缓存：Redis 存 { data, expire_at }，过期后先返旧数据再后台刷新，避免击穿。
"""
import asyncio
import json
import time
from typing import List, Callable, Awaitable

from app.db.redis import get_redis_client
from app.schemas.hotspot import CollectTrendObject

HOT_TRENDS_PREFIX = "hot_trends:"
LOCK_SUFFIX = ":lock"
LOGICAL_TTL_SECONDS = 60
LOCK_TTL_SECONDS = 30


def _cache_key(platforms: List[str], max_results: int) -> str:
    platform_key = ",".join(sorted(platforms)) if platforms else "youtube"
    return f"{HOT_TRENDS_PREFIX}{platform_key}:{max_results}"


def _serialize(data: List[CollectTrendObject]) -> str:
    return json.dumps(
        {"data": [x.model_dump(mode="json") for x in data], "expire_at": time.time() + LOGICAL_TTL_SECONDS},
        ensure_ascii=False,
    )


def _deserialize(raw: str) -> tuple[List[CollectTrendObject], float]:
    obj = json.loads(raw)
    data = [CollectTrendObject.model_validate(x) for x in obj["data"]]
    expire_at = float(obj.get("expire_at", 0))
    return data, expire_at

# 从缓存中获取热点信息
async def get_hot_trends_cached(
    platforms: List[str],
    max_results: int,
    loader: Callable[[List[str], int], Awaitable[List[CollectTrendObject]]],
) -> List[CollectTrendObject]:
    """
    逻辑过期缓存：有数据且未过期则直接返回；过期则先返旧数据并触发单飞刷新；缺失则加锁回源。
    loader: async (platforms, max_results) -> List[CollectTrendObject]
    """
    key = _cache_key(platforms or ["youtube"], max_results)
    lock_key = key + LOCK_SUFFIX
    redis = get_redis_client()

    try:
        #尝试获取缓存里的热点数据
        raw = await redis.get(key)
    except Exception:
        raw = None
    # 缓存里面有数据
    if raw:
        try:
            data, expire_at = _deserialize(raw)
            #查看数据是否过期
            if time.time() < expire_at:
                # 数据未过期，直接返回
                print("热点视频缓存命中")
                return data
            # 逻辑已过期：先返旧数据，再尝试抢锁刷新。只有没有lock_key的时候,acquired才为true，会抢到锁
            acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
            # 抢到锁之后，异步执行刷新缓存的任务
            if acquired:
                asyncio.create_task(_refresh_and_set(redis, key, lock_key, platforms or ["youtube"], max_results, loader))
            print("热点视频缓存命中，但已过期")
            return data
        except (json.JSONDecodeError, KeyError, Exception):
            pass

    # 缓存缺失：没有缓存的时候，等待缓存回填之后再返回数据
    print("热点视频缓存未命中")
    while True:
        acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            break
        await asyncio.sleep(0.2)
        raw = await redis.get(key)
        if raw:
            data, _ = _deserialize(raw)
            return data

    try:
        data = await loader(platforms or ["youtube"], max_results)
        # 键 TTL 设长一些，便于逻辑过期后仍能读到旧数据
        await redis.set(key, _serialize(data), ex=LOGICAL_TTL_SECONDS * 3)
        return data
    finally:
        # 释放锁
        await redis.delete(lock_key)


async def _refresh_and_set(
    redis,
    key: str,
    lock_key: str,
    platforms: List[str],
    max_results: int,
    loader: Callable[[List[str], int], Awaitable[List[CollectTrendObject]]],
) -> None:
    try:
        data = await loader(platforms, max_results)
        await redis.set(key, _serialize(data), ex=LOGICAL_TTL_SECONDS * 3)
    except Exception:
        pass
    finally:
        try:
            await redis.delete(lock_key)
        except Exception:
            pass

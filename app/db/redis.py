# 导入变成 redis.asyncio
import redis.asyncio as redis
from app.core.config import get_settings

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """
    返回全局共享的异步 Redis 客户端（内置连接池）。
    首次调用时惰性初始化，后续复用同一实例。
    """
    global _client
    if _client is not None:
        return _client

    settings = get_settings()

    if settings.USE_MOCK_REDIS:
        try:
            from fakeredis import aioredis
            _client = aioredis.FakeRedis(decode_responses=True)
            return _client
        except ImportError:
            # Fallback or error
            pass

    pool = redis.ConnectionPool(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
        max_connections=20,
    )
    _client = redis.Redis(connection_pool=pool)
    return _client


async def close_redis() -> None:
    """关闭全局 Redis 客户端及其连接池，仅在应用关闭时调用。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

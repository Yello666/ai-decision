# 导入变成 redis.asyncio
import redis.asyncio as redis
from app.core.config import get_settings

def get_redis_client() -> redis.Redis:
    settings = get_settings()
    
    if settings.USE_MOCK_REDIS:
        try:
            import fakeredis
            from fakeredis import aioredis
            return aioredis.FakeRedis(decode_responses=True)
        except ImportError:
            # Fallback or error
            pass

    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        decode_responses=True,
        password=settings.REDIS_PASSWORD,
    )

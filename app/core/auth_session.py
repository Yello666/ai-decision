"""Refresh-token 会话管理。

使用 Redis 存储“当前有效的 refresh jti”，为 refresh token 轮换/登出提供状态化能力：

- 登录成功：``register_refresh_jti(store_id, jti)``
- 刷新成功：先撤销旧 jti，再注册新 jti，实现真实轮换（旧 refresh 立即失效）
- 登出：``revoke_refresh_jti(store_id, jti)`` 或 ``revoke_all_for_merchant(store_id)``
- 校验：``is_refresh_jti_active(store_id, jti)``

Key 设计：``refresh:{store_id}:{jti}`` → "1"；TTL = refresh token 剩余有效期。
"""

from __future__ import annotations

import logging

from app.core.security import get_refresh_ttl_seconds
from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)


def _key(store_id: str, jti: str) -> str:
    return f"refresh:{store_id}:{jti}"


def _pattern(store_id: str) -> str:
    return f"refresh:{store_id}:*"


async def register_refresh_jti(store_id: str, jti: str) -> None:
    """登记一个有效 refresh jti，TTL 与 refresh token 保持一致。"""
    redis = get_redis_client()
    await redis.setex(_key(store_id, jti), get_refresh_ttl_seconds(), "1")


async def is_refresh_jti_active(store_id: str, jti: str) -> bool:
    """判断 refresh jti 是否仍被允许使用。"""
    redis = get_redis_client()
    value = await redis.get(_key(store_id, jti))
    return value is not None


async def revoke_refresh_jti(store_id: str, jti: str) -> None:
    """撤销指定 refresh jti（轮换或登出时调用）。"""
    redis = get_redis_client()
    await redis.delete(_key(store_id, jti))


async def revoke_all_for_merchant(store_id: str) -> int:
    """撤销某商户的全部 refresh 会话（踢下线全部设备）。返回被清理的数量。"""
    redis = get_redis_client()
    pattern = _pattern(store_id)
    deleted = 0
    async for key in redis.scan_iter(match=pattern, count=200):
        await redis.delete(key)
        deleted += 1
    if deleted:
        logger.info("Revoked %d refresh session(s) for store_id=%s", deleted, store_id)
    return deleted

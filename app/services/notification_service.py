"""
实时通知服务：通过 Redis Pub/Sub 将视频生成状态变更推送到 WebSocket 客户端。

Channel 命名规范: gen:status:{shopify_store_id}
每个商户一个独立 channel，保证多租户隔离。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "gen:status"


def _channel_name(store_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{store_id}"


async def publish_generation_status(
    store_id: str,
    generation_id: int,
    status: str,
    video_url: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """
    向该商户的 Redis channel 发布一条状态变更消息。
    WebSocket 订阅端收到后会实时推送给前端。
    """
    message: dict[str, Any] = {
        "event": "generation_status",
        "generation_id": generation_id,
        "status": status,
    }
    if video_url:
        message["video_url"] = video_url
    if error_message:
        message["error_message"] = error_message

    channel = _channel_name(store_id)
    try:
        redis_client = get_redis_client()
        receivers = await redis_client.publish(channel, json.dumps(message))
        logger.info(
            "Redis PUBLISH -> %s, receivers=%d, generation_id=%d, status=%s",
            channel, receivers, generation_id, status,
        )
    except Exception:
        logger.exception("Redis publish failed, channel=%s", channel)

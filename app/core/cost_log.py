"""模型调用 token / Seedance 用量成本日志（写入独立 logger `cost`，见 logger.configure_logging）。"""
from __future__ import annotations

import logging
from typing import Any

COST_LOG_TTL_SECONDS = 30 * 24 * 3600
SEEDANCE_COST_REDIS_KEY_PREFIX = "cost_logged:seedance:"


def get_cost_logger() -> logging.Logger:
    return logging.getLogger("cost")


def _pick_usage_value(usage: Any, *keys: str) -> int | None:
    if usage is None:
        return None
    for k in keys:
        if isinstance(usage, dict):
            v = usage.get(k)
        else:
            v = getattr(usage, k, None)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def log_llm_usage(scene_label: str, usage: Any, model: str | None = None) -> None:
    """记录 Qwen/OpenAI 兼容 chat.completions 的用量。"""
    if not usage:
        return
    prompt = _pick_usage_value(usage, "prompt_tokens")
    completion = _pick_usage_value(usage, "completion_tokens")
    total = _pick_usage_value(usage, "total_tokens")
    parts = [f"[{scene_label}]消耗token"]
    if total is not None:
        parts.append(f"total={total}")
    if prompt is not None:
        parts.append(f"prompt={prompt}")
    if completion is not None:
        parts.append(f"completion={completion}")
    if model:
        parts.append(f"model={model}")
    if len(parts) == 1:
        return
    msg = " ".join(parts)
    get_cost_logger().info(msg)


def _seedance_usage_nonempty(usage: dict[str, Any]) -> bool:
    t = usage.get("total_tokens")
    c = usage.get("completion_tokens")
    return t is not None or c is not None


async def try_log_seedance_usage(
    task_id: str,
    status: str,
    usage: dict[str, Any] | None,
) -> None:
    """仅在 succeeded 且 usage 含 token 字段时写入；Redis NX 防止回调与轮询重复记账。"""
    if status != "succeeded" or not task_id:
        return
    if not usage or not isinstance(usage, dict):
        return
    if not _seedance_usage_nonempty(usage):
        return

    key = f"{SEEDANCE_COST_REDIS_KEY_PREFIX}{task_id}"
    try:
        from app.db.redis import get_redis_client

        r = get_redis_client()
        acquired = await r.set(key, "1", nx=True, ex=COST_LOG_TTL_SECONDS)
        if not acquired:
            return
    except Exception:
        # Redis 不可用时仍记录，可能与另一路径重复各一条
        pass

    total = usage.get("total_tokens")
    completion = usage.get("completion_tokens")
    parts = [
        f"[Seedance2生成视频][task_id={task_id}]消耗token",
    ]
    if total is not None:
        parts.append(f"total={total}")
    if completion is not None:
        parts.append(f"completion={completion}")
    get_cost_logger().info(" ".join(parts))

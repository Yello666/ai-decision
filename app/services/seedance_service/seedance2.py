"""
Seedance 2.0 视频生成服务 — 火山引擎方舟 API 完整实现。

官方文档: https://www.volcengine.com/docs/82379/1520757
鉴权方式: API Key 请求头鉴权 (Authorization: Bearer $API_KEY)

代码结构:
  1. 配置区:              _get_api_key / _get_model_id / _get_base_url / _get_headers
  2. 请求体构造函数:      build_content_array / build_request_payload
  3. 发送请求函数:        create_video_task
  4. 异步查询任务状态:    query_video_task / poll_video_task
  5. 响应解析逻辑:        parse_task_response

API 端点:
  创建任务: POST  {base_url}/contents/generations/tasks
  查询任务: GET   {base_url}/contents/generations/tasks/{id}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.seedance2 import Seedance2VideoRequest

logger = logging.getLogger(__name__)


# ====================================================================
# 1. 配置区
#    model_id、api_key、endpoint 均从 Settings 读取，代码中不硬编码。
#    用户需在 .env 中填写:
#      SEEDANCE2_API_KEY=<你的方舟API Key>
#      SEEDANCE2_MODEL_ID=<你的推理接入点 ID 或模型 ID>
#      SEEDANCE2_API_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3
# ====================================================================

def _get_api_key() -> str:
    """获取 Seedance 2.0 API Key，未配置时抛出 ValueError。"""
    key = get_settings().SEEDANCE2_API_KEY
    if not key:
        raise ValueError(
            "SEEDANCE2_API_KEY 未配置，请在 .env 中设置有效的火山方舟 API Key"
        )
    return key


def _get_model_id() -> str:
    """获取 Seedance 2.0 Model ID / Endpoint ID，未配置时抛出 ValueError。"""
    model_id = get_settings().SEEDANCE2_MODEL_ID
    if not model_id:
        raise ValueError(
            "SEEDANCE2_MODEL_ID 未配置，请在 .env 中设置 "
            "seedance2.0 或 seedance2.0 fast 对应的推理接入点 ID"
        )
    return model_id


def _get_base_url() -> str:
    """获取 API Base URL，去除尾部斜杠。"""
    return get_settings().SEEDANCE2_API_ENDPOINT.rstrip("/")


def _get_headers() -> dict[str, str]:
    """构造 API Key 请求头鉴权 Headers。"""
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


# ====================================================================
# 2. 请求体构造函数
#    严格遵守官方固定 JSON 结构、type/role 枚举值、嵌套层级。
# ====================================================================

def build_content_array(req: Seedance2VideoRequest) -> list[dict[str, Any]]:
    """
    根据生成模式构造官方 content 数组。

    content 数组顺序: text → 图片(首帧/尾帧/参考图) → 视频 → 音频
    各 item 严格使用官方固定格式，不修改 type/role 值。

    官方媒体 JSON 结构:
      参考图片: {"type":"image_url","image_url":{"url":"..."},"role":"reference_image"}
      参考视频: {"type":"video_url","video_url":{"url":"..."},"role":"reference_video"}
      参考音频: {"type":"audio_url","audio_url":{"url":"..."},"role":"reference_audio"}
      首帧:     role="first_frame"
      尾帧:     role="last_frame"
    """
    content: list[dict[str, Any]] = []

    # 文本项始终为 content 数组第一个元素（type=text，必填）
    content.append({"type": "text", "text": req.prompt})

    if req.mode == "text_to_video":
        pass

    elif req.mode == "first_frame":
        content.append({
            "type": "image_url",
            "image_url": {"url": req.first_frame_url},
            "role": "first_frame",
        })

    elif req.mode == "first_last_frame":
        content.append({
            "type": "image_url",
            "image_url": {"url": req.first_frame_url},
            "role": "first_frame",
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": req.last_frame_url},
            "role": "last_frame",
        })

    elif req.mode == "multimodal_reference":
        for url in req.reference_image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            })
        for url in req.reference_video_urls:
            content.append({
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            })
        for url in req.reference_audio_urls:
            content.append({
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            })

    return content


def build_request_payload(req: Seedance2VideoRequest) -> dict[str, Any]:
    """
    构造完整的火山方舟官方 API 请求体。

    必填字段: model, content
    可选字段: callback_url, return_last_frame
             execution_expires_after, generate_audio, tools,
             safety_identifier, resolution, ratio, duration,
             seed, watermark

    所有字段名、大小写、数据类型严格与官方一致:
      - model:                    string
      - content:                  array
      - callback_url:             string
      - return_last_frame:        boolean
      - execution_expires_after:  integer (秒，默认 172800)
      - generate_audio:           boolean
      - tools:                    array
      - safety_identifier:        string
      - resolution:               string ("480p" / "720p")
      - ratio:                    string ("16:9" / "9:16" / "1:1" / "3:4" / "4:3" / "21:9" / "adaptive")
      - duration:                 integer (4~15)
      - seed:                     integer (-1 为随机)
      - watermark:                boolean
    """
    payload: dict[str, Any] = {
        "model": _get_model_id(),
        "content": build_content_array(req),
    }

    if req.ratio is not None:
        payload["ratio"] = req.ratio
    if req.duration is not None:
        payload["duration"] = req.duration
    if req.resolution is not None:
        payload["resolution"] = req.resolution
    if req.watermark is not None:
        payload["watermark"] = req.watermark
    if req.generate_audio is not None:
        payload["generate_audio"] = req.generate_audio
    if req.seed is not None:
        payload["seed"] = req.seed
    if req.callback_url is not None:
        payload["callback_url"] = req.callback_url
    if req.return_last_frame is not None:
        payload["return_last_frame"] = req.return_last_frame
    if req.execution_expires_after is not None:
        payload["execution_expires_after"] = req.execution_expires_after
    if req.tools is not None:
        payload["tools"] = req.tools
    if req.safety_identifier is not None:
        payload["safety_identifier"] = req.safety_identifier

    return payload


# ====================================================================
# 3. 发送请求函数
#    POST {base_url}/contents/generations/tasks
#    含指数退避重试（429 / 5xx），最多 3 次。
# ====================================================================

_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


async def create_video_task(req: Seedance2VideoRequest) -> dict[str, Any]:
    """
    创建 Seedance 2.0 视频生成任务（异步）。

    请求: POST {base_url}/contents/generations/tasks
    返回官方响应:
      {
        "id": "cgt-xxx",
        "model": "...",
        "status": "submitted",
        "created_at": 1234567890
      }

    Raises:
        ValueError: API Key / Model ID 未配置
        httpx.HTTPStatusError: 上游返回不可重试的错误
        RuntimeError: 超过最大重试次数
    """
    url = f"{_get_base_url()}/contents/generations/tasks"
    payload = build_request_payload(req)
    headers = _get_headers()

    logger.info(
        "Seedance 2.0 creating task: mode=%s, model=%s",
        req.mode, payload["model"],
    )

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)

            if resp.is_success:
                data = resp.json()
                logger.info(
                    "Seedance 2.0 task created: id=%s status=%s",
                    data.get("id"), data.get("status"),
                )
                return data

            err_detail: Any = resp.text
            try:
                err_detail = resp.json()
            except Exception:
                pass

            retryable = resp.status_code in _RETRYABLE_STATUS_CODES
            logger.error(
                "Seedance 2.0 create task failed: "
                "status=%s body=%s attempt=%s/%s",
                resp.status_code, err_detail, attempt, _MAX_RETRIES,
            )
            if not retryable or attempt == _MAX_RETRIES:
                resp.raise_for_status()

            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(
                "Seedance 2.0 create task transient error: %s attempt=%s/%s",
                type(exc).__name__, attempt, _MAX_RETRIES,
            )
            if attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

    raise RuntimeError("Seedance 2.0 任务提交失败：超过最大重试次数")


# ====================================================================
# 4. 异步查询任务状态逻辑
#    GET {base_url}/contents/generations/tasks/{id}
#    支持单次查询和轮询至终态。
# ====================================================================

TERMINAL_STATUSES = frozenset({
    "succeeded", "failed", "expired", "cancelled", "completed",
})


async def query_video_task(task_id: str) -> dict[str, Any]:
    """
    单次查询 Seedance 2.0 视频生成任务状态。

    请求: GET {base_url}/contents/generations/tasks/{id}
    返回官方响应:
      {
        "id": "cgt-xxx",
        "model": "...",
        "status": "queued|running|succeeded|failed|expired|cancelled",
        "created_at": ...,
        "updated_at": ...,
        "content": {"video_url": "...", "last_frame_url": "..."},
        "usage": {"completion_tokens": ..., "total_tokens": ...},
        "error": {"code": "...", "message": "..."}
      }
    """
    url = f"{_get_base_url()}/contents/generations/tasks/{task_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers())

    if not resp.is_success:
        err_detail: Any = resp.text
        try:
            err_detail = resp.json()
        except Exception:
            pass
        logger.error(
            "Seedance 2.0 query task failed: "
            "status=%s body=%s task_id=%s",
            resp.status_code, err_detail, task_id,
        )
        resp.raise_for_status()

    return resp.json()


async def poll_video_task(
    task_id: str,
    *,
    interval: float = 10.0,
    max_wait: float = 600.0,
) -> dict[str, Any]:
    """
    轮询查询任务状态直至终态（succeeded / failed / expired / cancelled）。

    Args:
        task_id:  创建任务时返回的 id
        interval: 轮询间隔（秒），默认 10
        max_wait: 最大等待时间（秒），默认 600

    Returns:
        终态完整响应 dict

    Raises:
        TimeoutError: 超过 max_wait 仍未到达终态
    """
    elapsed = 0.0
    while elapsed < max_wait:
        result = await query_video_task(task_id)
        status = result.get("status", "")

        if status in TERMINAL_STATUSES:
            logger.info(
                "Seedance 2.0 task %s reached terminal status: %s",
                task_id, status,
            )
            return result

        logger.info(
            "Seedance 2.0 task %s status=%s, next poll in %.1fs…",
            task_id, status, interval,
        )
        await asyncio.sleep(interval)
        elapsed += interval

    raise TimeoutError(
        f"Seedance 2.0 任务 {task_id} 轮询超时（已等待 {max_wait}s）"
    )


# ====================================================================
# 5. 响应解析逻辑
#    提取关键字段供上层使用。
# ====================================================================

def parse_task_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    解析官方任务响应，提取关键信息。

    返回结构化 dict:
      {
        "task_id":        str,
        "model":          str | None,
        "status":         str,
        "created_at":     int | None,
        "updated_at":     int | None,
        "video_url":      str | None,  (succeeded 时有值)
        "last_frame_url": str | None,  (return_last_frame=true 时有值)
        "usage":          dict | None,
        "error":          dict | None,
      }
    """
    result: dict[str, Any] = {
        "task_id": data.get("id"),
        "model": data.get("model"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }

    # 视频 URL 优先从 content.video_url 取（官方标准结构）
    content = data.get("content")
    if isinstance(content, dict):
        result["video_url"] = content.get("video_url")
        result["last_frame_url"] = content.get("last_frame_url")
    else:
        result["video_url"] = data.get("video_url")
        result["last_frame_url"] = None

    usage = data.get("usage")
    if isinstance(usage, dict):
        result["usage"] = usage
    else:
        result["usage"] = None

    error = data.get("error")
    if isinstance(error, dict):
        result["error"] = error
    else:
        result["error"] = None

    return result


# ====================================================================
# 内置标准多模态参考生视频示例
# 严格使用官方提示词格式 + 官方固定媒体 JSON 结构
# ====================================================================

EXAMPLE_MULTIMODAL_PAYLOAD: dict[str, Any] = {
    "model": "<YOUR_SEEDANCE2_MODEL_ID>",
    "content": [
        {
            "type": "text",
            "text": (
                "以[图1]为首帧，画面放大至飞机舷窗外，"
                "一团团云朵缓缓飘至画面中，其中一朵为彩色糖豆点缀的云朵，"
                "始终在画面中居中，然后缓缓变形为[图2]中的冰淇淋，"
                "镜头推远回到机舱内，坐在床边的[图3]中的角色伸手从窗外拿进冰淇淋，"
                "吃了一口，嘴巴上沾满奶油，脸上洋溢出甜蜜的笑容，"
                "此时视频配音为音频1，背景参考视频1的运镜风格。"
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/ref_image_1.jpg"},
            "role": "reference_image",
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/ref_image_2.jpg"},
            "role": "reference_image",
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/ref_image_3.jpg"},
            "role": "reference_image",
        },
        {
            "type": "video_url",
            "video_url": {"url": "https://example.com/ref_video_1.mp4"},
            "role": "reference_video",
        },
        {
            "type": "audio_url",
            "audio_url": {"url": "https://example.com/ref_audio_1.mp3"},
            "role": "reference_audio",
        },
    ],
    "ratio": "16:9",
    "duration": 10,
    "resolution": "720p",
    "watermark": False,
    "generate_audio": True,
    "seed": -1,
    "return_last_frame": False,
    "callback_url": None,
    "execution_expires_after": 172800,
}

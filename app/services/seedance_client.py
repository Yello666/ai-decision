"""
SeedDance 2.0 API 客户端：视频生成、图片生成、轮询视频状态。
文档：https://seedance2.app/api/v1
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    settings = get_settings()
    key = settings.SEEDANCE_API_KEY or ""
    if not key:
        raise ValueError("SEEDANCE_API_KEY 未配置")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return get_settings().SEEDANCE_BASE_URL.rstrip("/")


def start_video_generation(
    prompt: str,
    *,
    model: str = "doubao-seedance-1-5-pro",
    generation_type: str = "text_to_video",
    image_url: Optional[str] = None,
    aspect_ratio: str = "16:9",
    duration: float = 5,
    resolution: str = "720p",
) -> dict[str, Any]:
    """
    发起视频生成任务。返回上游 data（含 video_id）或抛出异常。
    """
    url = f"{_base_url()}/generate"
    payload: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "generation_type": generation_type,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "resolution": resolution,
    }
    if image_url and generation_type == "image_to_video":
        payload["image_url"] = image_url

    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        err = data["error"]
        raise RuntimeError(err.get("message", "SeedDance video generation failed"))
    return data.get("data") or {}


def upload_reference_image(image_file: Any) -> str:
    """
    上传参考图到 SeedDance，返回 hosted URL。
    用于 image_to_video 模式的 image_url 参数。
    image_file: 文件对象（有 .read() 方法）或 (filename, fileobj) 元组。
    """
    settings = get_settings()
    key = settings.SEEDANCE_API_KEY or ""
    if not key:
        raise ValueError("SEEDANCE_API_KEY 未配置")
    url = f"{_base_url()}/upload"
    headers = {"Authorization": f"Bearer {key}"}
    # multipart/form-data 不设置 Content-Type，让 requests 自动添加 boundary
    files = {"image": image_file}
    resp = requests.post(url, headers=headers, files=files, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        err = data["error"]
        raise RuntimeError(err.get("message", "SeedDance upload failed"))
    # 兼容多种返回格式：data.url / data.image_url / url / image_url
    inner = data.get("data") or {}
    result = inner.get("url") or inner.get("image_url") or data.get("url") or data.get("image_url")
    if not result or not isinstance(result, str):
        raise RuntimeError("SeedDance upload 未返回有效 URL")
    return result


def get_video_status(video_id: str) -> dict[str, Any]:
    """
    查询视频生成状态。返回上游 data（含 status, video_url 等）。
    """
    url = f"{_base_url()}/videos/{video_id}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        err = data["error"]
        raise RuntimeError(err.get("message", "SeedDance get video failed"))
    return data.get("data") or {}


def start_image_generation(
    prompt: str,
    *,
    model: Optional[str] = None,
    resolution: str = "2k",
    aspect_ratio: str = "1:1",
    output_format: str = "png",
    reference_image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    发起图片生成任务。返回上游 data（可能含 task_id 或直接 image_url，以文档为准）。
    model 未传或为已废弃的 nano-banana-2 时，使用配置 SEEDANCE_IMAGE_MODEL。
    """
    settings = get_settings()
    if model is None or not model.strip() or model.strip() == "nano-banana-2":
        model = settings.SEEDANCE_IMAGE_MODEL
    else:
        model = model.strip()
    url = f"{_base_url()}/generate-image"
    payload: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "output_format": output_format,
    }
    # 只传真实 URL：过滤掉占位符如 "string" 或空串
    valid_refs = [
        u for u in (reference_image_urls or [])
        if u and isinstance(u, str) and u.strip().startswith(("http://", "https://"))
    ]
    if valid_refs:
        payload["images"] = valid_refs

    resp = requests.post(url, headers=_headers(), json=payload, timeout=60)
    if not resp.ok:
        err_detail = resp.text
        try:
            err_detail = resp.json()
        except Exception:
            pass
        logger.error(
            "SeedDance generate-image failed: status=%s body=%s payload_prompt_len=%s",
            resp.status_code, err_detail, len(prompt),
        )
        resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        err = data["error"]
        raise RuntimeError(err.get("message", "SeedDance image generation failed"))
    return data.get("data") or {}

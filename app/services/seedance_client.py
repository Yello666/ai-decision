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

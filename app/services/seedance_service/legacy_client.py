"""
SeedDance 2.0 API 客户端：视频生成、图片生成、轮询视频状态（异步 httpx）。
文档：https://www.volcengine.com/docs/82379/1520757?lang=zh
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Union

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)



#
# # ==========================================================
# # Seedance 1.5 Pro — 火山引擎方舟视频生成 API
# # 文档: https://www.volcengine.com/docs/82379/1520757
# # Base URL: https://ark.cn-beijing.volces.com/api/v3
# # ==========================================================
#
# def _volcengine_headers() -> dict[str, str]:
#     settings = get_settings()
#     key = settings.SEEDANCE_VIDEO_API_KEY or ""
#     if not key or key == "your-seedance-video-api-key-placeholder":
#         raise ValueError("SEEDANCE_VIDEO_API_KEY 未配置，请在 .env 中设置有效的 API Key")
#     return {
#         "Authorization": f"Bearer {key}",
#         "Content-Type": "application/json",
#     }
#
#
# def _volcengine_base_url() -> str:
#     return get_settings().VOLCENGINE_BASE_URL.rstrip("/")

#
# async def create_seedance_video_task(payload: dict[str, Any]) -> dict[str, Any]:
#     """
#     创建视频生成任务。
#     POST {base_url}/contents/generations/tasks
#     请求体与火山引擎官方 API 一致（model, content, ratio, duration, watermark）。
#     返回 {"id": "cgt-xxx", "status": "submitted"}。
#     """
#     url = f"{_volcengine_base_url()}/contents/generations/tasks"
#     retries = 3
#     for attempt in range(1, retries + 1):
#         try:
#             async with httpx.AsyncClient(timeout=30) as client:
#                 resp = await client.post(url, headers=_volcengine_headers(), json=payload)
#             if resp.is_success:
#                 return resp.json()
#
#             err_detail = resp.text
#             try:
#                 err_detail = resp.json()
#             except Exception:
#                 pass
#
#             retryable = resp.status_code in {429, 500, 502, 503, 504}
#             logger.error(
#                 "Seedance 1.5 Pro create task failed: status=%s body=%s attempt=%s/%s",
#                 resp.status_code, err_detail, attempt, retries,
#             )
#             if not retryable or attempt == retries:
#                 resp.raise_for_status()
#             await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
#         except (httpx.TimeoutException, httpx.ConnectError) as e:
#             logger.warning(
#                 "Seedance 1.5 Pro create task transient error: %s attempt=%s/%s",
#                 type(e).__name__, attempt, retries,
#             )
#             if attempt == retries:
#                 raise
#             await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
#
#     raise RuntimeError("Seedance 任务提交失败：超过最大重试次数")
#
#
# async def query_seedance_video_task(task_id: str) -> dict[str, Any]:
#     """
#     查询视频生成任务状态。
#     GET {base_url}/contents/generations/tasks/{id}
#     返回 {model, status, created_at, updated_at, content: {video_url, last_frame_url}, usage, error}。
#     """
#     url = f"{_volcengine_base_url()}/contents/generations/tasks/{task_id}"
#     async with httpx.AsyncClient(timeout=15) as client:
#         resp = await client.get(url, headers=_volcengine_headers())
#     if not resp.is_success:
#         err_detail = resp.text
#         try:
#             err_detail = resp.json()
#         except Exception:
#             pass
#         logger.error(
#             "Seedance 1.5 Pro query task failed: status=%s body=%s task_id=%s",
#             resp.status_code, err_detail, task_id,
#         )
#         resp.raise_for_status()
#     return resp.json()
#


# def _headers() -> dict[str, str]:
#     settings = get_settings()
#     key = settings.SEEDANCE_API_KEY or ""
#     if not key:
#         raise ValueError("SEEDANCE_API_KEY 未配置")
#     return {
#         "Authorization": f"Bearer {key}",
#         "Content-Type": "application/json",
#     }



# async def start_video_generation(
#     prompt: str,
#     *,
#     model: str = "doubao-seedance-1-5-pro",
#     generation_type: str = "text_to_video",
#     image_url: Optional[str] = None,
#     aspect_ratio: str = "16:9",
#     duration: float = 5,
#     resolution: str = "720p",
# ) -> dict[str, Any]:
#     """
#     发起视频生成任务。返回上游 data（含 video_id）或抛出异常。
#     """
#     url = f"{_base_url()}/generate"
#     payload: dict[str, Any] = {
#         "prompt": prompt,
#         "model": model,
#         "generation_type": generation_type,
#         "aspect_ratio": aspect_ratio,
#         "duration": duration,
#         "resolution": resolution,
#     }
#     if image_url and generation_type == "image_to_video":
#         payload["image_url"] = image_url

#     async with httpx.AsyncClient(timeout=30) as client:
#         resp = await client.post(url, headers=_headers(), json=payload)
#     resp.raise_for_status()
#     data = resp.json()
#     if data.get("error"):
#         err = data["error"]
#         raise RuntimeError(err.get("message", "SeedDance video generation failed"))
#     return data.get("data") or {}



# async def get_video_status(video_id: str) -> dict[str, Any]:
#     """
#     查询视频生成状态。返回上游 data（含 status, video_url 等）。
#     """
#     url = f"{_base_url()}/videos/{video_id}"
#     async with httpx.AsyncClient(timeout=15) as client:
#         resp = await client.get(url, headers=_headers())
#     resp.raise_for_status()
#     data = resp.json()
#     if data.get("error"):
#         err = data["error"]
#         raise RuntimeError(err.get("message", "SeedDance get video failed"))
#     return data.get("data") or {}


# async def start_image_generation(
#     prompt: str,
#     *,
#     model: Optional[str] = None,
#     resolution: str = "2k",
#     aspect_ratio: str = "1:1",
#     output_format: str = "png",
#     reference_image_urls: Optional[list[str]] = None,
# ) -> dict[str, Any]:
#     """
#     发起图片生成任务。返回上游 data（可能含 task_id 或直接 image_url，以文档为准）。
#     """
#     settings = get_settings()
#     if model is None or not model.strip() or model.strip() == "nano-banana-2":
#         model = settings.SEEDANCE_IMAGE_MODEL
#     else:
#         model = model.strip()
#     url = f"{_base_url()}/generate-image"
#     payload: dict[str, Any] = {
#         "prompt": prompt,
#         "model": model,
#         "resolution": resolution,
#         "aspect_ratio": aspect_ratio,
#         "output_format": output_format,
#     }
#     valid_refs = [
#         u for u in (reference_image_urls or [])
#         if u and isinstance(u, str) and u.strip().startswith(("http://", "https://"))
#     ]
#     if valid_refs:
#         payload["images"] = valid_refs

#     async with httpx.AsyncClient(timeout=60) as client:
#         resp = await client.post(url, headers=_headers(), json=payload)
#     if not resp.ok:
#         err_detail = resp.text
#         try:
#             err_detail = resp.json()
#         except Exception:
#             pass
#         logger.error(
#             "SeedDance generate-image failed: status=%s body=%s payload_prompt_len=%s",
#             resp.status_code, err_detail, len(prompt),
#         )
#         resp.raise_for_status()
#     data = resp.json()
#     if data.get("error"):
#         err = data["error"]
#         raise RuntimeError(err.get("message", "SeedDance image generation failed"))
#     return data.get("data") or {}


# def _base_url() -> str:
#     return get_settings().SEEDANCE_BASE_URL.rstrip("/")

#
# async def upload_reference_image(
#     file_content: Union[bytes, Any],
#     filename: str = "image.png",
# ) -> str:
#     """
#     上传参考图到 SeedDance，返回 hosted URL。
#     用于 image_to_video 模式的 image_url 参数。
#     file_content: 文件字节内容，或带 .read() 的文件对象（同步读）。
#     """
#     settings = get_settings()
#     key = settings.SEEDANCE_API_KEY or ""
#     if not key:
#         raise ValueError("SEEDANCE_API_KEY 未配置")
#     if hasattr(file_content, "read"):
#         file_content = file_content.read()
#     url = f"{_base_url()}/upload"
#     headers = {"Authorization": f"Bearer {key}"}
#     files = {"image": (filename, file_content)}
#
#     async with httpx.AsyncClient(timeout=30) as client:
#         resp = await client.post(url, headers=headers, files=files)
#     resp.raise_for_status()
#     data = resp.json()
#     if data.get("error"):
#         err = data["error"]
#         raise RuntimeError(err.get("message", "SeedDance upload failed"))
#     inner = data.get("data") or {}
#     result = inner.get("url") or inner.get("image_url") or data.get("url") or data.get("image_url")
#     if not result or not isinstance(result, str):
#         raise RuntimeError("SeedDance upload 未返回有效 URL")
#     return result
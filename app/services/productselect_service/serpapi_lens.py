"""调用 SerpApi Google Lens，按图片 URL 搜同款/相似商品，返回完整原始响应。

按需求「能返回的信息都返回」，这里把 SerpApi 的整个响应原样转成 dict 返回，
不做裁剪，方便后续按需取用（价格、评分、评论数、来源、同款链接、related_content 等）。
"""

from __future__ import annotations

import logging
from typing import Any

import serpapi

from . import config

logger = logging.getLogger(__name__)


def _to_plain_dict(results: Any) -> dict[str, Any]:
    """把 SerpApi 返回对象转成普通 dict（兼容不同版本）。"""
    if hasattr(results, "as_dict"):
        try:
            return dict(results.as_dict())
        except Exception:
            pass
    try:
        return dict(results)
    except Exception:
        return {"raw": str(results)}


def search_by_image_url(
    image_url: str,
    lens_type: str | None = None,
) -> dict[str, Any]:
    """对公网图片 URL 调 Google Lens，返回完整响应 dict。"""
    api_key = config.get_serpapi_api_key()
    if not api_key:
        raise RuntimeError("缺少 SERPAPI_API_KEY：请在项目根 .env 配置。")

    params: dict[str, Any] = {
        "engine": "google_lens",
        "url": image_url,
    }
    effective_type = lens_type if lens_type is not None else config.SERPAPI_LENS_TYPE
    if effective_type:
        params["type"] = effective_type
    if config.SERPAPI_LENS_COUNTRY:
        params["country"] = config.SERPAPI_LENS_COUNTRY

    client = serpapi.Client(api_key=api_key)
    results = client.search(params)
    data = _to_plain_dict(results)

    visual = data.get("visual_matches")
    logger.info(
        "Google Lens 返回 visual_matches=%s related_content=%s",
        len(visual) if isinstance(visual, list) else 0,
        len(data.get("related_content") or []) if isinstance(data.get("related_content"), list) else 0,
    )
    return data

"""SerpApi Google Lens 结果精简。

Lens 负责“召回”相似商品；这里不再做额外打分，只按 SerpApi 原始顺序去重后取前 N 条，
压缩成产品接口可直接展示的 top_matches。完整 Lens 原始响应仍可存文件/数据库 raw_json。
"""

from __future__ import annotations

from typing import Any


def _price_info(item: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    price = item.get("price")
    if not isinstance(price, dict):
        return None, None, None
    value = price.get("extracted_value")
    try:
        numeric = float(value) if value is not None else None
    except (TypeError, ValueError):
        numeric = None
    return numeric, price.get("currency"), price.get("value")


def build_top_matches(
    lens_response: dict[str, Any] | None,
    *,
    category: str | None = None,
    related_ip: str | None = None,
    attributes: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """从完整 Lens 响应里按原始顺序提取前 N 个展示用匹配结果。"""
    if not isinstance(lens_response, dict):
        return []

    matches = lens_response.get("visual_matches")
    if not isinstance(matches, list):
        return []

    out: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    for item in matches:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        numeric_price, currency, price_text = _price_info(item)
        simplified = {
            "rank": len(out) + 1,
            "title": item.get("title"),
            "source": item.get("source"),
            "price": numeric_price,
            "currency": currency,
            "price_text": price_text,
            "in_stock": item.get("in_stock") if isinstance(item.get("in_stock"), bool) else None,
            "rating": item.get("rating"),
            "reviews": item.get("reviews"),
            "link": link,
            "thumbnail": item.get("thumbnail"),
            "image": item.get("image"),
            "position": item.get("position"),
        }
        out.append(simplified)
        if len(out) >= max(1, limit):
            break
    return out


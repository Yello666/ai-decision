"""SerpApi Google Lens 结果筛选。

Lens 负责“召回”相似商品，结果会很多且有噪音；这里把完整原始结果压缩成产品接口可直接展示的
top_matches（默认前三条）。完整 Lens 原始响应仍可存文件/数据库 raw_json。
"""

from __future__ import annotations

from typing import Any


_TRUSTED_SOURCES = (
    "falcons",
    "official",
    "amazon",
    "taobao",
    "ebay",
    "aliexpress",
    "walmart",
    "target",
    "nike",
    "puma",
    "adidas",
)


def _tokens(value: str) -> list[str]:
    raw = value.replace("（", " ").replace("）", " ").replace("(", " ").replace(")", " ")
    out: list[str] = []
    for token in raw.replace("/", " ").replace("-", " ").replace("_", " ").split():
        t = token.strip().lower()
        if len(t) >= 3:
            out.append(t)
    return out


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


def _score_match(
    item: dict[str, Any],
    *,
    category: str | None,
    related_ip: str | None,
    attributes: list[str] | None,
) -> float:
    title = str(item.get("title") or "")
    source = str(item.get("source") or "")
    haystack = f"{title} {source}".lower()

    score = 0.0

    # Lens 原始排序仍有价值，越靠前基础分越高
    try:
        pos = int(item.get("position") or 999)
    except (TypeError, ValueError):
        pos = 999
    score += max(0, 30 - min(pos, 30))

    numeric_price, _, _ = _price_info(item)
    if numeric_price is not None:
        score += 18

    if item.get("in_stock") is True:
        score += 5

    if any(src in haystack for src in _TRUSTED_SOURCES):
        score += 10

    keywords: list[str] = []
    if category:
        keywords.extend(_tokens(category))
    if related_ip and related_ip != "未知":
        keywords.extend(_tokens(related_ip))
    for attr in attributes or []:
        keywords.extend(_tokens(str(attr)))

    # 去重，避免同一词重复刷分
    seen: set[str] = set()
    for kw in keywords:
        if kw in seen:
            continue
        seen.add(kw)
        if kw in haystack:
            score += 8

    return score


def build_top_matches(
    lens_response: dict[str, Any] | None,
    *,
    category: str | None = None,
    related_ip: str | None = None,
    attributes: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """从完整 Lens 响应里提取前 N 个展示用匹配结果。"""
    if not isinstance(lens_response, dict):
        return []

    matches = lens_response.get("visual_matches")
    if not isinstance(matches, list):
        return []

    ranked: list[tuple[float, dict[str, Any]]] = []
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
            "rank": 0,  # 排序后回填
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
        score = _score_match(
            item,
            category=category,
            related_ip=related_ip,
            attributes=attributes,
        )
        simplified["rank_score"] = round(score, 2)
        ranked.append((score, simplified))

    ranked.sort(key=lambda x: x[0], reverse=True)
    out = [item for _, item in ranked[: max(1, limit)]]
    for idx, item in enumerate(out, start=1):
        item["rank"] = idx
    return out


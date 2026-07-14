"""Evaluate Google Lens matches and derive product opportunity estimates.

SerpApi is used only for recall. This module selects a small set of candidate
matches, asks Qwen-VL to judge visual/text similarity, then derives a draft
profile from reliable reference products.
"""

from __future__ import annotations

import base64
import json
import logging
import math
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import OpenAI

from . import config

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_MAX_EVAL_IMAGE_BYTES = 6 * 1024 * 1024
_IMAGE_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

_EVALUATION_PROMPT = """你是电商选品的相似商品审核员。
任务：判断“商品机会图片”与若干“候选相似商品”是否真的是同类/同款/高相似商品，并基于可信候选给出价格、尺寸、重量预估。

重要规则：
1. SerpApi 只是召回，不能默认认为候选一定准确。
2. 优先比较商品主体本身，不要被背景、人物姿势、颜色氛围误导。
3. 如果候选只是颜色相近、场景相近、人物相近，但品类不同，应判为低相似或不可用。
4. 价格预估只能使用你认为足够相似的候选；低相似候选不要参与估价。
5. Amazon 候选可以作为较重要参考，但仍必须经过相似度判断。
6. 尺寸/重量如果候选信息不足，可以结合品类和视觉估计，无法判断则填 null。
7. 成本价不是零售价，若只能从零售价反推，请给保守区间并在 notes 说明。

严格输出 JSON，不要 markdown，不要解释。所有 similarity_score 必须返回 0～100 的整数或小数，例如 85；
不要返回 0.85、0.8 等 0～1 的比例值。结构：
{
  "matches": [
    {
      "candidate_key": "候选 key，必须原样返回",
      "visual_similarity_score": 0,
      "keyword_similarity_score": 0,
      "final_similarity_score": 0,
      "similarity_level": "high | medium | low | reject",
      "is_reference_used": true,
      "reason": "为什么像或不像，是否可用于估价"
    }
  ],
  "profile_estimate": {
    "selling_price_min": null,
    "selling_price_max": null,
    "cost_price_min": null,
    "cost_price_max": null,
    "currency": "USD",
    "length_cm": null,
    "width_cm": null,
    "height_cm": null,
    "volume_cm3": null,
    "weight_value": null,
    "weight_unit": null,
    "notes": "说明用了哪些候选、哪些被排除，以及估算依据"
  }
}"""


def _get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client
    api_key = config.get_vl_api_key()
    if not api_key:
        raise RuntimeError("缺少 DashScope API Key：请配置 DASHSCOPE_API_KEY 或 LLM_API_KEY。")
    _client = OpenAI(
        api_key=api_key,
        base_url=config.VL_BASE_URL,
        http_client=httpx.Client(proxy=None),
    )
    return _client


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                cleaned = rest
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("相似商品评估 JSON 解析失败，原始输出片段：%s", cleaned[:300])
        return {}


def _price_info(item: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    price = item.get("price")
    if not isinstance(price, dict):
        return None, None, None
    value = price.get("extracted_value")
    try:
        numeric = float(value) if value is not None else None
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None and numeric <= 0:
        numeric = None
    currency = price.get("currency")
    currency = str(currency).upper() if currency else None
    return numeric, currency, price.get("value")


def _candidate_image(item: dict[str, Any]) -> str | None:
    image = item.get("image") or item.get("thumbnail")
    return image if isinstance(image, str) and image.startswith(("http://", "https://")) else None


def _candidate_image_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for value in (item.get("image"), item.get("thumbnail")):
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)
    return urls


def _guess_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _download_image_as_data_url(url: str | None, *, label: str) -> str | None:
    """Download an image ourselves so Qwen-VL does not fetch fragile external URLs."""
    if not url:
        return None
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=_IMAGE_DOWNLOAD_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
    except Exception:
        logger.warning("相似商品评估图片下载失败 label=%s url=%s", label, url, exc_info=True)
        return None

    if not data:
        logger.warning("相似商品评估图片为空 label=%s url=%s", label, url)
        return None
    if len(data) > _MAX_EVAL_IMAGE_BYTES:
        logger.warning("相似商品评估图片过大 label=%s size=%s url=%s", label, len(data), url)
        return None

    content_type = ""
    try:
        content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    except Exception:
        content_type = ""
    mime = content_type if content_type.startswith("image/") else _guess_image_mime(data)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        mime = _guess_image_mime(data)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        logger.warning("相似商品评估图片格式不支持 label=%s content_type=%s url=%s", label, content_type, url)
        return None

    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _normal_reviews(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def _normal_rating(value: Any) -> float | None:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 0 <= rating <= 5 else None


def _is_amazon(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").lower()
    link = str(item.get("link") or "").lower()
    host = urlparse(link).netloc.lower()
    return "amazon" in source or "amazon." in host


def _keyword_bonus(item: dict[str, Any], object_context: dict[str, Any]) -> float:
    title = str(item.get("title") or "").lower()
    if not title:
        return 0.0
    words: set[str] = set()
    for key in ("category", "related_ip", "description"):
        value = object_context.get(key)
        if isinstance(value, str):
            words.update(part.strip().lower() for part in value.replace("/", " ").split() if len(part.strip()) >= 3)
    attrs = object_context.get("attributes")
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, str):
                words.update(part.strip().lower() for part in attr.replace("/", " ").split() if len(part.strip()) >= 3)
    if not words:
        return 0.0
    hits = sum(1 for word in words if word in title)
    return min(15.0, hits * 5.0)


def _quality_score(item: dict[str, Any], object_context: dict[str, Any]) -> float:
    numeric_price, _, _ = _price_info(item)
    reviews = _normal_reviews(item.get("reviews"))
    rating = _normal_rating(item.get("rating"))
    score = 0.0
    if numeric_price is not None:
        score += 35.0
    if reviews is not None and reviews > 0:
        score += min(25.0, 6.0 + math.log10(reviews + 1) * 8.0)
    if rating is not None:
        score += min(15.0, rating / 5.0 * 15.0)
    if _candidate_image(item):
        score += 15.0
    if item.get("in_stock") is True:
        score += 5.0
    score += _keyword_bonus(item, object_context)
    if _is_amazon(item):
        score += 8.0
    return round(score, 2)


def _simplify_item(item: dict[str, Any], *, source_rank: int, role: str, object_context: dict[str, Any]) -> dict[str, Any]:
    numeric_price, currency, price_text = _price_info(item)
    return {
        "candidate_key": f"c{source_rank}",
        "source_rank": source_rank,
        "selection_role": role,
        "selection_score": _quality_score(item, object_context),
        "title": item.get("title"),
        "source": item.get("source"),
        "price": numeric_price,
        "currency": currency,
        "price_text": price_text,
        "rating": _normal_rating(item.get("rating")),
        "reviews": _normal_reviews(item.get("reviews")),
        "in_stock": item.get("in_stock") if isinstance(item.get("in_stock"), bool) else None,
        "link": item.get("link"),
        "thumbnail": item.get("thumbnail"),
        "image": item.get("image"),
        "position": item.get("position"),
        "is_amazon": _is_amazon(item),
        "raw": item,
    }


def _unique_visual_matches(lens_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(lens_response, dict):
        return []
    matches = lens_response.get("visual_matches")
    if not isinstance(matches, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in matches:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(item)
    return out


def _select_candidates(lens_response: dict[str, Any] | None, object_context: dict[str, Any]) -> list[dict[str, Any]]:
    matches = _unique_visual_matches(lens_response)
    if not matches:
        return []

    selected: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    first = _simplify_item(matches[0], source_rank=1, role="first_result", object_context=object_context)
    selected.append(first)
    seen_links.add(str(first.get("link")))

    ranked = [
        _simplify_item(item, source_rank=idx, role="quality_selected", object_context=object_context)
        for idx, item in enumerate(matches[1:5], start=2)
    ]
    ranked.sort(key=lambda item: item["selection_score"], reverse=True)
    for candidate in ranked[:2]:
        link = str(candidate.get("link"))
        if link and link not in seen_links:
            selected.append(candidate)
            seen_links.add(link)

    amazon_candidates = [
        _simplify_item(item, source_rank=idx, role="amazon_reference", object_context=object_context)
        for idx, item in enumerate(matches, start=1)
        if _is_amazon(item)
    ]
    amazon_candidates.sort(key=lambda item: item["selection_score"], reverse=True)
    for candidate in amazon_candidates:
        link = str(candidate.get("link"))
        if link and link not in seen_links:
            selected.append(candidate)
            break

    for idx, candidate in enumerate(selected, start=1):
        candidate["selection_order"] = idx
    return selected[:4]


def _call_qwen_vl(
    *,
    object_image_url: str,
    object_context: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    object_data_url = _download_image_as_data_url(object_image_url, label="object")
    candidate_data_urls: dict[str, str] = {}
    candidate_image_sources: dict[str, str] = {}
    for item in candidates:
        key = item["candidate_key"]
        for image_url in _candidate_image_urls(item):
            data_url = _download_image_as_data_url(image_url, label=key)
            if data_url:
                candidate_data_urls[key] = data_url
                candidate_image_sources[key] = image_url
                break

    payload = {
        "object": {
            "category": object_context.get("category"),
            "related_ip": object_context.get("related_ip"),
            "description": object_context.get("description"),
            "attributes": object_context.get("attributes"),
            "reason": object_context.get("reason"),
            "image_available": object_data_url is not None,
        },
        "candidates": [
            {
                "candidate_key": item["candidate_key"],
                "selection_role": item["selection_role"],
                "source_rank": item["source_rank"],
                "title": item.get("title"),
                "source": item.get("source"),
                "price": item.get("price"),
                "currency": item.get("currency"),
                "price_text": item.get("price_text"),
                "rating": item.get("rating"),
                "reviews": item.get("reviews"),
                "is_amazon": item.get("is_amazon"),
                "image_available": item["candidate_key"] in candidate_data_urls,
                "image_source_url": candidate_image_sources.get(item["candidate_key"]),
            }
            for item in candidates
        ],
    }
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _EVALUATION_PROMPT},
        {"type": "text", "text": "商品机会与候选元数据：\n" + json.dumps(payload, ensure_ascii=False)},
        {"type": "text", "text": "商品机会图片："},
    ]
    if object_data_url:
        content.append({"type": "image_url", "image_url": {"url": object_data_url}})
    else:
        content.append({"type": "text", "text": "商品机会图片下载失败，本次只能结合商品机会文本信息与候选信息判断。"})

    for item in candidates:
        data_url = candidate_data_urls.get(item["candidate_key"])
        if not data_url:
            content.append({"type": "text", "text": f"候选 {item['candidate_key']} 图片下载失败或不可用，只能使用标题/价格/评论信息。"})
            continue
        content.append({"type": "text", "text": f"候选 {item['candidate_key']} 图片："})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    response = _get_client().chat.completions.create(
        model=config.VL_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
    )
    text = response.choices[0].message.content if response.choices else ""
    result = _parse_json(text or "")
    result["token_usage"] = _extract_usage(response)
    return result


def _extract_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "model": config.VL_MODEL,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _fallback_evaluation(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for item in candidates:
        score = min(80.0, max(30.0, float(item.get("selection_score") or 0)))
        matches.append(
            {
                "candidate_key": item["candidate_key"],
                "visual_similarity_score": None,
                "keyword_similarity_score": None,
                "final_similarity_score": score,
                "similarity_level": "medium" if score >= 60 else "low",
                "is_reference_used": score >= 60 and item.get("price") is not None,
                "reason": "Qwen-VL 评估失败，临时使用 SerpApi 质量分兜底。",
            }
        )
    return {
        "matches": matches,
        "profile_estimate": _estimate_profile_from_prices(candidates, matches),
        "fallback": True,
    }


def _normal_score(value: Any) -> float | None:
    """统一模型可能返回的 0～1 比例分数与 0～100 百分制分数。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    # 兼容模型未遵守提示词、将 80% 写成 0.8 的情况。
    if 0 < parsed <= 1:
        parsed *= 100
    return max(0.0, min(100.0, parsed))


def _normal_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _merge_evaluation(candidates: list[dict[str, Any]], qwen_result: dict[str, Any]) -> list[dict[str, Any]]:
    by_key = {
        item.get("candidate_key"): item
        for item in qwen_result.get("matches") or []
        if isinstance(item, dict) and item.get("candidate_key")
    }
    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        raw = by_key.get(candidate["candidate_key"], {})
        final_score = _normal_score(raw.get("final_similarity_score"))
        visual_score = _normal_score(raw.get("visual_similarity_score"))
        keyword_score = _normal_score(raw.get("keyword_similarity_score"))
        if final_score is None:
            parts = [score for score in (visual_score, keyword_score) if score is not None]
            final_score = sum(parts) / len(parts) if parts else min(80.0, float(candidate.get("selection_score") or 0))
        is_reference_used = _normal_bool(raw.get("is_reference_used")) and candidate.get("price") is not None
        merged.append(
            {
                **candidate,
                "visual_similarity_score": visual_score,
                "keyword_similarity_score": keyword_score,
                "final_similarity_score": round(final_score, 2),
                "similarity_level": raw.get("similarity_level") or ("high" if final_score >= 80 else "medium" if final_score >= 60 else "low"),
                "is_reference_used": is_reference_used,
                "evaluation_reason": raw.get("reason") or "模型未返回明确原因。",
            }
        )
    return merged


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _clean_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    weight_unit = profile.get("weight_unit")
    weight_unit = str(weight_unit).strip().lower() if weight_unit else None
    if weight_unit not in {"g", "kg", "lb", "oz"}:
        weight_unit = None
    weight_value = _float_or_none(profile.get("weight_value"))
    if weight_value is None:
        weight_unit = None
    currency = str(profile.get("currency") or "USD").strip().upper() or "USD"
    return {
        "selling_price_min": _float_or_none(profile.get("selling_price_min")),
        "selling_price_max": _float_or_none(profile.get("selling_price_max")),
        "cost_price_min": _float_or_none(profile.get("cost_price_min")),
        "cost_price_max": _float_or_none(profile.get("cost_price_max")),
        "currency": currency,
        "length_cm": _float_or_none(profile.get("length_cm")),
        "width_cm": _float_or_none(profile.get("width_cm")),
        "height_cm": _float_or_none(profile.get("height_cm")),
        "volume_cm3": _float_or_none(profile.get("volume_cm3")),
        "weight_value": weight_value,
        "weight_unit": weight_unit,
        "notes": str(profile.get("notes") or "").strip() or None,
    }


def _estimate_profile_from_prices(candidates: list[dict[str, Any]], evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    used_keys = {
        item.get("candidate_key")
        for item in evaluations
        if isinstance(item, dict) and item.get("is_reference_used")
    }
    prices = [
        float(item["price"])
        for item in candidates
        if item.get("candidate_key") in used_keys and isinstance(item.get("price"), (int, float)) and item["price"] > 0
    ]
    if not prices:
        return {}
    prices.sort()
    low = prices[0]
    high = prices[-1]
    if len(prices) == 1:
        low *= 0.9
        high *= 1.1
    return {
        "selling_price_min": round(low, 2),
        "selling_price_max": round(high, 2),
        "cost_price_min": round(low * 0.25, 2),
        "cost_price_max": round(high * 0.45, 2),
        "currency": "USD",
        "notes": "基于通过相似度筛选的 SerpApi/Amazon 参考商品价格生成；成本价为按零售价反推的保守区间。",
    }


def _has_profile_data(profile: dict[str, Any]) -> bool:
    return any(
        profile.get(key) is not None
        for key in (
            "selling_price_min",
            "selling_price_max",
            "cost_price_min",
            "cost_price_max",
            "length_cm",
            "width_cm",
            "height_cm",
            "volume_cm3",
            "weight_value",
            "notes",
        )
    )


def _reference_evaluations(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in evaluations
        if item.get("is_reference_used") and isinstance(item.get("price"), (int, float)) and item["price"] > 0
    ]


def _profile_confidence(evaluations: list[dict[str, Any]], profile: dict[str, Any]) -> float | None:
    references = _reference_evaluations(evaluations)
    if not references:
        return None
    # 可信度主要看参与估算的商品数量、相似度和价格区间稳定性。
    avg_similarity = sum(float(item["final_similarity_score"]) for item in references) / len(references)
    count_score = min(100.0, 45.0 + len(references) * 18.0)
    confidence = avg_similarity * 0.65 + count_score * 0.25

    selling_min = _float_or_none(profile.get("selling_price_min"))
    selling_max = _float_or_none(profile.get("selling_price_max"))
    if selling_min and selling_max and selling_min > 0:
        spread = (selling_max - selling_min) / selling_min
        confidence += max(0.0, 10.0 - spread * 8.0)
    return round(max(0.0, min(100.0, confidence)), 2)


def _build_estimate_detail(candidates: list[dict[str, Any]], evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {item["candidate_key"]: item for item in candidates}
    used: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in evaluations:
        candidate = by_key.get(item["candidate_key"], {})
        target = used if item.get("is_reference_used") else rejected
        target.append(
            {
                "candidate_key": item["candidate_key"],
                "title": candidate.get("title"),
                "source": candidate.get("source"),
                "price": candidate.get("price"),
                "currency": candidate.get("currency"),
                "selection_role": candidate.get("selection_role"),
                "final_similarity_score": item.get("final_similarity_score"),
                "reason": item.get("evaluation_reason"),
            }
        )
    return {
        "used_matches": used,
        "rejected_matches": rejected,
        "price_method": "使用 Qwen-VL 判定可参考的相似商品零售价生成售价区间，并按零售价保守反推成本区间。",
    }


def _opportunity_score(
    *,
    object_context: dict[str, Any],
    evaluations: list[dict[str, Any]],
    profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not evaluations:
        return None

    potential_base = {
        "high": 80.0,
        "medium": 60.0,
        "low": 40.0,
    }.get(str(object_context.get("ecommerce_potential") or "").lower(), 55.0)
    scores = [float(item["final_similarity_score"]) for item in evaluations if item.get("final_similarity_score") is not None]
    reference_scores = [
        float(item["final_similarity_score"])
        for item in evaluations
        if item.get("is_reference_used") and item.get("final_similarity_score") is not None
    ]
    if not scores:
        return None

    best_similarity = max(scores)
    avg_reference = sum(reference_scores) / len(reference_scores) if reference_scores else 35.0
    confidence = _float_or_none((profile or {}).get("confidence_score")) or 35.0
    amazon_bonus = 5.0 if any(item.get("is_reference_used") and item.get("is_amazon") for item in evaluations) else 0.0
    potential_part = potential_base * 0.30
    best_similarity_part = best_similarity * 0.30
    reference_part = avg_reference * 0.22
    confidence_part = confidence * 0.15
    score = potential_part + best_similarity_part + reference_part + confidence_part + amazon_bonus
    score = round(max(0.0, min(100.0, score)), 2)
    level = "high" if score >= 75 else "medium" if score >= 55 else "low"
    # 保留逐项计算过程，供前端评分说明 Tooltip 直接展示。
    reason = (
        f"识图潜力基础分：{potential_base:.0f} × 30% = {potential_part:.2f}\n"
        f"最高相似度：{best_similarity:.2f} × 30% = {best_similarity_part:.2f}\n"
        f"参考商品平均相似度：{avg_reference:.2f} × 22% = {reference_part:.2f}"
        f"（采用 {len(reference_scores)} 个）\n"
        f"预估可信度：{confidence:.2f} × 15% = {confidence_part:.2f}\n"
        f"Amazon 参考加分：{amazon_bonus:.2f}\n"
        f"最终评分：{score:.2f}（{level}）"
    )
    return {
        "score": score,
        "level": level,
        "reason": reason,
    }


def _top_match_dict(row: Any) -> dict[str, Any]:
    raw = row.raw_json if isinstance(row.raw_json, dict) else {}
    evaluation = raw.get("evaluation") if isinstance(raw.get("evaluation"), dict) else {}
    price_text = None
    price = raw.get("price") if isinstance(raw.get("price"), dict) else None
    if isinstance(price, dict):
        price_text = price.get("value")
    return {
        "id": row.id,
        "rank": evaluation.get("selection_order") or raw.get("position"),
        "title": row.title,
        "source": row.store,
        "price": float(row.price) if row.price is not None else None,
        "currency": row.currency,
        "price_text": price_text,
        "in_stock": row.in_stock,
        "rating": float(row.rating) if row.rating is not None else None,
        "reviews": row.reviews,
        "link": row.url,
        "thumbnail": row.thumbnail_url,
        "image": raw.get("image"),
        "position": raw.get("position"),
        "match_level": row.match_level,
        "selection_order": row.selection_order or evaluation.get("selection_order"),
        "selection_role": row.selection_role or evaluation.get("selection_role"),
        "selection_score": float(row.selection_score) if row.selection_score is not None else evaluation.get("selection_score"),
        "visual_similarity_score": (
            float(row.visual_similarity_score)
            if row.visual_similarity_score is not None
            else evaluation.get("visual_similarity_score")
        ),
        "keyword_similarity_score": (
            float(row.keyword_similarity_score)
            if row.keyword_similarity_score is not None
            else evaluation.get("keyword_similarity_score")
        ),
        "final_similarity_score": (
            float(row.final_similarity_score)
            if row.final_similarity_score is not None
            else evaluation.get("final_similarity_score")
        ),
        "similarity_level": row.similarity_level or evaluation.get("similarity_level"),
        "is_reference_used": row.is_reference_used,
        "reason": row.selection_reason or evaluation.get("reason"),
    }


def evaluate_lens_matches(
    *,
    lens_response: dict[str, Any] | None,
    object_image_url: str,
    object_context: dict[str, Any],
    match_rows: list[Any],
) -> dict[str, Any]:
    """Select/evaluate Lens matches, annotate rows, and build profile kwargs."""
    candidates = _select_candidates(lens_response, object_context)
    if not candidates:
        return {"top_matches": [], "profile": None, "reference_match_id": None}

    try:
        qwen_result = _call_qwen_vl(
            object_image_url=object_image_url,
            object_context=object_context,
            candidates=candidates,
        )
    except Exception:
        logger.exception("Qwen-VL 相似商品评估失败，使用启发式兜底")
        qwen_result = _fallback_evaluation(candidates)

    evaluations = _merge_evaluation(candidates, qwen_result)
    if not qwen_result.get("profile_estimate"):
        qwen_result["profile_estimate"] = _estimate_profile_from_prices(candidates, evaluations)

    rows_by_url = {row.url: row for row in match_rows if row.url}
    top_rows: list[Any] = []
    reference_match_id: int | None = None
    for item in evaluations:
        row = rows_by_url.get(item.get("link"))
        if row is None:
            continue
        raw = row.raw_json if isinstance(row.raw_json, dict) else {}
        row.raw_json = {
            **raw,
            "evaluation": {
                "candidate_key": item["candidate_key"],
                "selection_order": item["selection_order"],
                "selection_role": item["selection_role"],
                "selection_score": item["selection_score"],
                "visual_similarity_score": item["visual_similarity_score"],
                "keyword_similarity_score": item["keyword_similarity_score"],
                "final_similarity_score": item["final_similarity_score"],
                "similarity_level": item["similarity_level"],
                "is_reference_used": item["is_reference_used"],
                "reason": item["evaluation_reason"],
            },
        }
        # 同步写结构化字段，避免前端和 SQL 查询依赖 raw_json 内部结构。
        row.selection_order = item["selection_order"]
        row.selection_role = item["selection_role"]
        row.selection_score = item["selection_score"]
        row.visual_similarity_score = item["visual_similarity_score"]
        row.keyword_similarity_score = item["keyword_similarity_score"]
        row.final_similarity_score = item["final_similarity_score"]
        row.similarity_level = item["similarity_level"]
        row.is_reference_used = bool(item["is_reference_used"])
        row.selection_reason = item["evaluation_reason"]
        if item["is_reference_used"]:
            row.match_level = "amazon_reference" if item.get("is_amazon") else "selected"
            if reference_match_id is None:
                reference_match_id = row.id
        else:
            row.match_level = "candidate"
        top_rows.append(row)

    profile = _clean_profile(qwen_result.get("profile_estimate"))
    if not _has_profile_data(profile):
        profile = _estimate_profile_from_prices(candidates, evaluations)
    if profile and _has_profile_data(profile):
        profile["confidence_score"] = _profile_confidence(evaluations, profile)
        profile["estimate_detail_json"] = _build_estimate_detail(candidates, evaluations)
        profile["source"] = "match"
        profile["status"] = "draft"
        profile["reference_match_id"] = reference_match_id
    else:
        profile = None
    opportunity = _opportunity_score(
        object_context=object_context,
        evaluations=evaluations,
        profile=profile,
    )

    return {
        "top_matches": [_top_match_dict(row) for row in top_rows],
        "profile": profile,
        "opportunity": opportunity,
        "reference_match_id": reference_match_id,
        "qwen_result": qwen_result,
    }

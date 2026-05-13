import json
import asyncio
import logging
import math
from typing import Any, Dict, List

import httpx

from openai import OpenAI, AsyncOpenAI
from sqlalchemy.orm import Session

from app.models import Hotspot
from app.core.config import get_settings
from app.schemas.hotspot import (
    HotspotMatchResponse,
    TrendObject,
    BrandObject,
    MatchRadar,
    recommendation_level_from_compatibility_score,
)
from app.services.hotspot_service.match_cache import (
    mget_match,
    set_match_many,
)

logger = logging.getLogger(__name__)
settings = get_settings()


# proxy=None：强制不走系统/环境变量代理，避免全局代理干扰国内大模型请求
LLM_CLIENT = OpenAI(
    base_url=settings.LLM_API_URL,
    api_key=settings.LLM_API_KEY,
    http_client=httpx.Client(proxy=None),
)
ASYNC_LLM_CLIENT = AsyncOpenAI(
    base_url=settings.LLM_API_URL,
    api_key=settings.LLM_API_KEY,
    http_client=httpx.AsyncClient(proxy=None),
)
MATCH_BATCH_SIZE = 10

# 契合度加权合成（与 HotspotMatchResponse.compatibility_score 描述保持一致）。
# marketing_risk 越高风险越大，故用 (100 - marketing_risk) 作为「安全性得分」参与加权。
MATCH_WEIGHT_BUSINESS_RELEVANCE = 0.35
MATCH_WEIGHT_AUDIENCE_OVERLAP = 0.25
MATCH_WEIGHT_BRAND_VOICE_FIT = 0.25
MATCH_WEIGHT_MARKETING_SAFETY = 0.15


def compute_compatibility_score(
    business_relevance: float,
    audience_overlap: float,
    brand_voice_fit: float,
    marketing_risk: float,
) -> float:
    """由四分维度合成 0-100 契合度总分（保留一位小数）。"""
    br = max(0.0, min(100.0, float(business_relevance)))
    ao = max(0.0, min(100.0, float(audience_overlap)))
    bvf = max(0.0, min(100.0, float(brand_voice_fit)))
    mr = max(0.0, min(100.0, float(marketing_risk)))
    safety = 100.0 - mr
    raw = (
        MATCH_WEIGHT_BUSINESS_RELEVANCE * br
        + MATCH_WEIGHT_AUDIENCE_OVERLAP * ao
        + MATCH_WEIGHT_BRAND_VOICE_FIT * bvf
        + MATCH_WEIGHT_MARKETING_SAFETY * safety
    )
    return round(max(0.0, min(100.0, raw)), 1)


def _clamp_dimension_0_100(value: Any) -> float:
    """与 compute_compatibility_score 一致，将雷达分项限制在 0–100，避免缓存/模型产出越界导致 Pydantic 校验失败。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(x):
        return 0.0
    return max(0.0, min(100.0, x))


# --------------------------
# V3：面向"单品牌 + 多热点"的批量 LLM 匹配（品牌信息只写一次；带 (brand,trend) 结果缓存）
# --------------------------
async def batch_match_hotspot_for_brand_async(
    trends: List[TrendObject],
    brand: BrandObject,
) -> List[HotspotMatchResponse]:
    """
    给定当前商户的品牌信息，对一批热点并行批量调用 LLM 做匹配度分析。

    - 命中缓存的 (brand, trend) 组合直接复用历史结果，不再请求大模型。
    - 未命中的热点分成 MATCH_BATCH_SIZE 一批并行请求，品牌信息在 prompt 里只写一次。
    """
    if not trends:
        return []

    match_model = get_settings().LLM_MODEL_36_PLUS

    brand_fp, hit_map, miss_indices = await mget_match(brand, trends)
    logger.info(
        "热点匹配缓存 品牌=%s 总数=%d 命中=%d 未命中=%d",
        brand.name, len(trends), len(hit_map), len(miss_indices),
    )

    new_result_map: Dict[int, Dict[str, Any]] = {}
    if miss_indices:
        batches_indices = [
            miss_indices[i:i + MATCH_BATCH_SIZE]
            for i in range(0, len(miss_indices), MATCH_BATCH_SIZE)
        ]
        batch_llm_results = await asyncio.gather(
            *[
                _llm_match_single_brand_async(
                    brand=brand,
                    trends=[trends[i] for i in group],
                    llm_model=match_model,
                )
                for group in batches_indices
            ],
            return_exceptions=True,
        )
        for group, llm_results in zip(batches_indices, batch_llm_results):
            if isinstance(llm_results, Exception) or not llm_results:
                raise RuntimeError(f"批量匹配 LLM 调用失败或为空: {llm_results}")
            for local_idx, res in enumerate(llm_results):
                if local_idx >= len(group):
                    break
                new_result_map[group[local_idx]] = res

    responses: List[HotspotMatchResponse] = []
    for idx, trend in enumerate(trends):
        raw = hit_map.get(idx) or new_result_map.get(idx)
        if not raw:
            raise ValueError(f"缺少热点匹配结果，index={idx}")
        try:
            responses.append(_build_match_response(brand, trend, raw))
        except Exception:
            logger.exception(
                "组装单条热点匹配失败 品牌=%s index=%d title=%s from_cache=%s raw_keys=%s",
                brand.name,
                idx,
                (trend.title or "")[:300],
                idx in hit_map,
                sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
            )
            raise

    # 必须在整批结果校验通过后再写入缓存，否则 LLM 产出越界会先落库，后续请求一直 500
    if new_result_map:
        await set_match_many(brand_fp, trends, new_result_map)

    return responses


async def _llm_match_single_brand_async(
    brand: BrandObject,
    trends: List[TrendObject],
    llm_model: str,
) -> List[Dict[str, Any]]:
    """
    单品牌 + 多热点批量匹配：品牌信息放在 system 消息中只写一次；
    user 消息中只列出热点数组，节省 token。

    返回 results 列表（与 trends 顺序对应）。失败抛异常由上层捕获。
    """
    system_prompt = f"""你是一个资深品牌营销专家，点评风格简洁到位。你将在下方【品牌信息】的上下文中，
逐一评估用户提供的多个热点与该品牌的契合度

【品牌信息】
名称：{brand.name}
主要售卖产品：{brand.mainly_sold_products}
品牌介绍：{brand.core_value or '未提供'}
品牌调性：{brand.tone}
目标受众：{', '.join(brand.audience) if brand.audience else '未提供'}

你将只为每个热点输出分项评分与文案；综合契合度总分由系统在服务端按固定权重自动计算，请勿输出 compatibility_score。

分项维度说明（均为 0-100 浮点数）：
- business_relevance：热点与品牌品类、产品、使用场景的「业务相关性」。是否存在自然、可信的产品或场景切入点（例如热点讨论芥末味咖啡而品牌正好卖咖啡，则该项应偏高）。
- audience_overlap：热点受众画像与品牌目标用户的重合度。
- brand_voice_fit：热点常见的表达方式、气质是否与品牌调性一致（同品类下，接地气热点配接地气品牌该项更高）。
- marketing_risk：营销风险；越高表示跟风蹭热点时越可能出现公关、舆情、合规或价值观层面的问题。

输出格式要求：
- 严格输出 JSON，不要任何多余解释/前后缀。
- 顶层是对象，包含 "results" 数组；数组长度必须等于输入热点数量，且顺序一致。
- 每个元素 schema：
{{
  "index": Integer,                    // 与输入 index 一致
  "business_relevance": Float,         // 0-100
  "audience_overlap": Float,         // 0-100
  "brand_voice_fit": Float,          // 0-100
  "marketing_risk": Float,           // 0-100，越高风险越大
  "suggestion": String,              // 若要借势该热点，建议从什么营销角度切入
  "reason": String,                  // 简短分析理由（为何给出上述分数）
  "risk_warning": String             // 可选：有明显风险时一两句风险提示；无则填空字符串 ""
}}
"""

    items_data = [
        {
            "index": i,
            "title": t.title,
            "summary": t.summary,
            "tags": t.tags,
            "audience": t.audience,
        }
        for i, t in enumerate(trends)
    ]
    user_prompt = (
        "【待评估热点列表】每项字段含义：title=热点标题，summary=摘要，tags=标签，"
        "audience=热点受众画像（可选）。\n"
        f"{json.dumps(items_data, ensure_ascii=False)}"
    )

    response = await ASYNC_LLM_CLIENT.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("模型返回内容为空")

    data = json.loads(content)
    results = data.get("results", []) or []
    results.sort(key=lambda x: int(x.get("index", 0)))

    normalized: List[Dict[str, Any]] = [{} for _ in trends]
    for res in results:
        try:
            idx = int(res.get("index", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(trends):
            normalized[idx] = res

    if any(not item for item in normalized):
        missing = [i for i, item in enumerate(normalized) if not item]
        raise ValueError(f"模型返回结果不完整，缺失 index: {missing}")

    return normalized


def _build_match_response(
    brand: BrandObject,
    trend: TrendObject,
    raw: Dict[str, Any],
) -> HotspotMatchResponse:
    """将 LLM/缓存中的 dict 结果转换为 HotspotMatchResponse。"""
    required = [
        "business_relevance",
        "audience_overlap",
        "brand_voice_fit",
        "marketing_risk",
        "suggestion",
        "reason",
    ]
    for key in required:
        if key not in raw:
            raise KeyError(f"缺少必要字段: {key}")

    radar = MatchRadar(
        business_relevance=_clamp_dimension_0_100(raw["business_relevance"]),
        audience_overlap=_clamp_dimension_0_100(raw["audience_overlap"]),
        brand_voice_fit=_clamp_dimension_0_100(raw["brand_voice_fit"]),
        marketing_risk=_clamp_dimension_0_100(raw["marketing_risk"]),
    )
    compatibility_score = compute_compatibility_score(
        radar.business_relevance,
        radar.audience_overlap,
        radar.brand_voice_fit,
        radar.marketing_risk,
    )

    rw = raw.get("risk_warning")
    risk_warning = rw if isinstance(rw, str) and rw.strip() else None

    return HotspotMatchResponse(
        brand_name=brand.name,
        trend_title=trend.title,
        compatibility_score=compatibility_score,
        recommendation=recommendation_level_from_compatibility_score(compatibility_score),
        radar=radar,
        reason=raw["reason"],
        suggestion=raw["suggestion"],
        risk_warning=risk_warning,
    )

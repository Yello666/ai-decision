import json
import asyncio
import logging
from typing import Any, Dict, List

import httpx

from openai import OpenAI, AsyncOpenAI
from sqlalchemy.orm import Session

from app.models import Hotspot
from app.core.config import get_settings
from app.schemas.hotspot import (
    HotspotLLMModel,

    HotspotMatchResponse,
    TrendObject,
    BrandObject,
    RecommendationLevel,
    MatchRadar
)
from app.services.hostpot_service.match_cache import (
    mget_match,
    set_match_many,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# 你可以用任何兼容 OpenAI API 的后端
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
DEFAULT_LLM_MODEL = HotspotLLMModel.qwen_35_plus
MATCH_BATCH_SIZE = 10

# --------------------------
# V3：面向"单品牌 + 多热点"的批量 LLM 匹配（品牌信息只写一次；带 (brand,trend) 结果缓存）
# --------------------------
async def batch_match_hotspot_for_brand_async(
    trends: List[TrendObject],
    brand: BrandObject,
    llm_model: HotspotLLMModel = DEFAULT_LLM_MODEL,
) -> List[HotspotMatchResponse]:
    """
    给定当前商户的品牌信息，对一批热点并行批量调用 LLM 做匹配度分析。

    - 命中缓存的 (brand, trend) 组合直接复用历史结果，不再请求大模型。
    - 未命中的热点分成 MATCH_BATCH_SIZE 一批并行请求，品牌信息在 prompt 里只写一次。
    """
    if not trends:
        return []

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
                    llm_model=llm_model,
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

        if new_result_map:
            await set_match_many(brand_fp, trends, new_result_map)

    responses: List[HotspotMatchResponse] = []
    for idx, trend in enumerate(trends):
        raw = hit_map.get(idx) or new_result_map.get(idx)
        if not raw:
            raise ValueError(f"缺少热点匹配结果，index={idx}")
        responses.append(_build_match_response(brand, trend, raw))
    return responses


async def _llm_match_single_brand_async(
    brand: BrandObject,
    trends: List[TrendObject],
    llm_model: HotspotLLMModel = DEFAULT_LLM_MODEL,
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
核心价值：{brand.core_value or '未提供'}
品牌调性：{brand.tone}
目标受众：{', '.join(brand.audience) if brand.audience else '未提供'}

输出格式要求：
- 严格输出 JSON，不要任何多余解释/前后缀。
- 顶层是对象，包含 "results" 数组；数组长度必须等于输入热点数量，且顺序一致。
- 每个元素 schema：
{{
  "index": Integer,                    // 与输入 index 一致
  "semantic_relevance": Float,         // 0-100
  "tone_fit": Float,                   // 0-100
  "audience_overlap": Float,           // 0-100
  "risk_index": Float,                 // 0-100 越高越危险
  "compatibility_score": Float,        // 0-100 综合契合度
  "recommendation": String,            // 强烈推荐|推荐|值得尝试|谨慎考虑|不建议|强烈不建议
  "suggestion": String,                // 结合热点的一句话营销切入点
  "reason": String                     // 简短分析理由
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
    user_prompt = f"【待评估热点列表】\n{json.dumps(items_data, ensure_ascii=False)}"

    response = await ASYNC_LLM_CLIENT.chat.completions.create(
        model=llm_model.value,
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
    required = ["compatibility_score", "recommendation", "suggestion", "reason",
                "semantic_relevance", "tone_fit", "audience_overlap", "risk_index"]
    for key in required:
        if key not in raw:
            raise KeyError(f"缺少必要字段: {key}")

    return HotspotMatchResponse(
        brand_name=brand.name,
        trend_title=trend.title,
        compatibility_score=float(raw["compatibility_score"]),
        recommendation=RecommendationLevel.from_str(raw["recommendation"]),
        radar=MatchRadar(
            semantic_relevance=float(raw["semantic_relevance"]),
            tone_fit=float(raw["tone_fit"]),
            audience_overlap=float(raw["audience_overlap"]),
            risk_index=float(raw["risk_index"]),
        ),
        reason=raw["reason"],
        suggestion=raw["suggestion"],
        risk_warning=raw.get("risk_warning"),
    )

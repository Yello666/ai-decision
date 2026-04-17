import os
import json
import logging
import asyncio
from typing import List, Dict, Any
from openai import OpenAI, AsyncOpenAI
from app.schemas.hotspot import (
    SentimentCN,
    CollectTrendObject
)
from app.services.hostpot_service.analysis_cache import (
    mget_analysis,
    set_analysis_many,
)
from app.services.trending_service import get_youtube_trends
from app.services.trending_service.get_youtube_trends import get_trending_videos_async

logger = logging.getLogger(__name__)

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
_LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-b0fc3528ced64aa4b31eca19eb10fb39")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")

LLM_CLIENT = OpenAI(base_url=_LLM_BASE_URL, api_key=_LLM_API_KEY)
ASYNC_LLM_CLIENT = AsyncOpenAI(base_url=_LLM_BASE_URL, api_key=_LLM_API_KEY)

BATCH_SIZE = 10


async def collect_and_format_hot_data_async(platforms: List[str], max_results: int = 5) -> List[CollectTrendObject]:
    """
    异步采集+清洗热点数据：按平台拉取并用 LLM 分析，返回 CollectTrendObject 列表。

    单热点去重：先查 Redis 中"该热点的历史分析结果"，命中直接复用；
    只对未命中的热点并行批量调用 LLM，新结果写回缓存。
    """
    result: List[CollectTrendObject] = []
    platform_list = platforms if platforms else ["youtube"]
    for platform in platform_list:
        if platform != "youtube":
            continue
        youtube_trend_list = await get_trending_videos_async(max_results)
        process_list = youtube_trend_list[:max_results]
        if not process_list:
            continue

        hit_map, miss_items = await mget_analysis(process_list)
        logger.info(
            "热点分析缓存 平台=%s 总数=%d 命中=%d 未命中=%d",
            platform, len(process_list), len(hit_map), len(miss_items),
        )

        new_analysis_map: Dict[str, Dict[str, Any]] = {}
        if miss_items:
            batches = [miss_items[i:i + BATCH_SIZE] for i in range(0, len(miss_items), BATCH_SIZE)]
            batch_payloads = [
                [
                    {"id": item.id, "title": item.title, "summary": item.summary, "tags": item.tags}
                    for item in batch
                ]
                for batch in batches
            ]
            batch_results_list = await asyncio.gather(
                *[_analyze_with_llm_async(payload) for payload in batch_payloads],
                return_exceptions=True,
            )
            for idx, batch_results in enumerate(batch_results_list):
                if isinstance(batch_results, Exception):
                    logger.warning("Batch LLM analysis raised exception at batch index %d: %s", idx, batch_results)
                    continue
                if not batch_results or "results" not in batch_results:
                    logger.warning("Batch LLM analysis failed or returned empty at batch index %d", idx)
                    continue
                for res in batch_results["results"]:
                    rid = res.get("id")
                    if rid:
                        new_analysis_map[rid] = res

            if new_analysis_map:
                await set_analysis_many(miss_items, new_analysis_map)

        for item in process_list:
            analysis_res = hit_map.get(item.id) or new_analysis_map.get(item.id)
            if not analysis_res:
                continue
            if _apply_analysis_to_item(item, analysis_res):
                result.append(item)
    return result


def _apply_analysis_to_item(item: CollectTrendObject, analysis_res: Dict[str, Any]) -> bool:
    """把 LLM 分析结果应用到 item。命中红线/不安全则跳过，返回 False。"""
    if analysis_res.get("risk_category") == "RED_LINE" or not analysis_res.get("is_safe_for_marketing"):
        logger.info(
            "Skipping unsafe content: %s (Reason: %s)",
            item.title, analysis_res.get("risk_category"),
        )
        return False
    item.summary = analysis_res.get("summary", item.summary)
    item.tags = analysis_res.get("tags", item.tags)
    item.sentiment_label = _map_sentiment(analysis_res.get("sentiment_label", "中性"))
    item.sentiment_score = analysis_res.get("sentiment_score", 0.0)
    item.risk_category = analysis_res.get("risk_category")
    item.warning_message = analysis_res.get("warning_message")
    if item.view_count == 0 and item.likes == 0:
        item.warning_message = "暂无更多信息，建议前往平台进行搜索。"
    item.audience = analysis_res.get("audience", [])
    return True

def _build_analysis_prompt(content_list: List[Dict[str, Any]]) -> str:
    return f"""
 Role：电商营销风控专家。你将接收一组YouTube热点摘要列表（包含id, title, summary, tags），需逐一分析每个热点的商业机会与风险。 
 
 Analysis Rules (Apply to EACH item in the list):
 1. 对热点进行总结，50-200字最佳；输出所给内容的summary和几个最主要的tags。
 2. Sentiment Analysis:
    - Label: 正面/中性/负面（如果既不是正面，也不是负面，那就是中性；既包含正面，也包含负面，也是中性）
    - Score: -100.0 (极负) 到 100.0 (极正) 
 3. Risk Category (Priority Logic):
    按顺序匹配，命中即止： 
    3.1 RED_LINE 绝对不可营销(is_safe_for_marketing=false): 
       涉及内容：政治斗争、国家政策批评、地缘冲突、台湾问题、中国政治、霸权、谋杀、恐怖主义、儿童剥削、极端暴力、色情、爱泼斯坦案、重大自然灾害。
    3.2 YELLOW_OPPORTUNITY 高商业潜力但需谨慎(is_safe_for_marketing=true): 
       特征：行业质量危机、服务投诉、权益纠纷。公众愤怒但渴求市场替代方案。
    3.3 GREEN_SAFE 低风险，可营销(is_safe_for_marketing=true): 
       特征：正面新闻、知识科普、轻松有趣、自嘲式幽默、搞笑梗等。
 4. Audience Inference: 推断3-5个具体的关注热点的人群标签 (如："18-35岁职场女性", "硬核科技粉")，拒绝泛词。
 
 Output Format:
 - Strict JSON only. No markdown, no explanations. 
 - The output must be a JSON object containing a "results" key, which is a list of objects corresponding exactly to the input items.
 - Schema for EACH object in "results": 
 {{
    "id": "String (Copy exactly from input 'id')",
    "summary": "String (Brief Summary)", 
    "tags": ["String", "String", ...],
    "sentiment_score": Float (-100.0 to 100.0), 
    "sentiment_label": "String (正面、中性、负面)", 
    "risk_category": "Enum[RED_LINE, YELLOW_OPPORTUNITY, GREEN_SAFE]", 
    "is_safe_for_marketing": Boolean, 
    "warning_message": "String", 
    "audience": ["String", "String", ...] 
 }}

 # Input Data 
 {json.dumps(content_list, ensure_ascii=False)}
"""


def _log_token_usage(usage) -> None:
    if usage:
        logger.info(
            "Token 消耗（%s）: prompt=%d, completion=%d, total=%d",
            LLM_MODEL, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
        )


async def _analyze_with_llm_async(content_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """异步调用大模型进行批量情感分析、风险评估和受众推断。"""
    prompt = _build_analysis_prompt(content_list)
    try:
        response = await ASYNC_LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        _log_token_usage(response.usage if hasattr(response, "usage") else None)
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.exception("Async LLM analysis failed: %s", e)
        return {}


def _analyze_with_llm(content_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同步调用大模型（保留给同步兼容函数使用）。"""
    prompt = _build_analysis_prompt(content_list)
    try:
        response = LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        _log_token_usage(response.usage if hasattr(response, "usage") else None)
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.exception("LLM analysis failed: %s", e)
        return {}


def _map_sentiment(label: str) -> SentimentCN:
    mapping = {
        "正面": SentimentCN.positive,
        "中性": SentimentCN.neutral,
        "负面": SentimentCN.negative,
    }
    return mapping.get(label, SentimentCN.neutral)

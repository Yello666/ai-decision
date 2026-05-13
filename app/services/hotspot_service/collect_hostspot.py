import json
import logging
import asyncio
from typing import List, Dict, Any

import httpx
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.schemas.hotspot import (
    SentimentCN,
    CollectTrendObject
)
from app.services.hotspot_service.analysis_cache import (
    mget_analysis,
    set_analysis_many,
)
from app.services.hotspot_service.get_youtube_trends import get_trending_videos_async

logger = logging.getLogger(__name__)
settings=get_settings()
LLM_MODEL = settings.LLM_MODEL_36_PLUS

_FALLBACK_NO_LLM_MSG = (
    "营销风控分析服务暂时不可用，以下为未经过模型审核的原始抓取数据，商业使用前请自行评估风险。"
)


def _with_llm_unavailable_notice(item: CollectTrendObject) -> CollectTrendObject:
    extra = _FALLBACK_NO_LLM_MSG
    if item.warning_message:
        extra = f"{item.warning_message} {extra}"
    return item.model_copy(update={"warning_message": extra})


# proxy=None：强制不走系统/环境变量代理，避免全局代理干扰国内大模型请求
def _llm_http_client() -> httpx.AsyncClient:
    t = float(settings.LLM_REQUEST_TIMEOUT_SECONDS)
    return httpx.AsyncClient(proxy=None, timeout=httpx.Timeout(t, connect=min(60.0, t)))


ASYNC_LLM_CLIENT = AsyncOpenAI(
    base_url=settings.LLM_API_URL,
    api_key=settings.LLM_API_KEY,
    http_client=_llm_http_client(),
)

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
        result.extend(await analyze_collect_trend_items_async(process_list))
    return result


async def analyze_collect_trend_items_async(items: List[CollectTrendObject]) -> List[CollectTrendObject]:
    """
    对已采集的热点做 LLM 分析并回填展示字段。

    YouTube 与 TikTok 都产出 CollectTrendObject 初始对象后复用这里，缓存粒度仍由
    platform/id/title/summary/tags 指纹控制。
    """
    if not items:
        return []

    hit_map, miss_items = await mget_analysis(items)
    platform_names = ",".join(sorted({item.platform for item in items}))
    logger.info(
        "热点分析缓存 平台=%s 总数=%d 命中=%d 未命中=%d",
        platform_names, len(items), len(hit_map), len(miss_items),
    )

    new_analysis_map: Dict[str, Dict[str, Any]] = {}
    if miss_items:
        batches = [miss_items[i:i + BATCH_SIZE] for i in range(0, len(miss_items), BATCH_SIZE)]
        batch_payloads = [
            [
                {
                    "id": item.id,
                    "platform": item.platform,
                    "title": item.title,
                    "summary": item.summary,
                    "tags": item.tags,
                }
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
                # JSON 里 id 常为数字，与 CollectTrendObject.id（字符串）不一致会导致合并失败、接口返回 []
                if rid is None or (isinstance(rid, str) and not rid.strip()):
                    continue
                new_analysis_map[str(rid).strip()] = res

        if new_analysis_map:
            await set_analysis_many(miss_items, new_analysis_map)

    expected_ids = {item.id for item in items}
    llm_ids = set(new_analysis_map.keys())
    if miss_items and not new_analysis_map:
        logger.warning(
            "热点分析 LLM 批次未写入任何结果（可能 JSON 解析失败或返回空），待分析条数=%d",
            len(miss_items),
        )
    elif miss_items and llm_ids and not (expected_ids & llm_ids):
        logger.warning(
            "热点分析 LLM 返回的 id 与输入不一致（将无法合并）。输入 id 样例=%s LLM id 样例=%s",
            list(expected_ids)[:5],
            list(llm_ids)[:5],
        )

    result: List[CollectTrendObject] = []
    skipped_no_analysis = 0
    skipped_unsafe = 0
    for item in items:
        analysis_res = hit_map.get(item.id) or new_analysis_map.get(item.id)
        if not analysis_res:
            skipped_no_analysis += 1
            continue
        if _apply_analysis_to_item(item, analysis_res):
            result.append(item)
        else:
            skipped_unsafe += 1

    logger.info(
        "热点分析汇总 入参=%d 输出=%d 缓存命中键数=%d LLM新键数=%d 无分析跳过=%d 风控跳过=%d",
        len(items),
        len(result),
        len(hit_map),
        len(new_analysis_map),
        skipped_no_analysis,
        skipped_unsafe,
    )
    if result:
        return result
    if items and skipped_unsafe == 0:
        logger.warning(
            "热点分析无任何可用 LLM 结果且无非拒项，降级返回未过模型的原始条目（条数=%d）",
            len(items),
        )
        return [_with_llm_unavailable_notice(i) for i in items]
    if items:
        logger.warning(
            "热点分析产出为空列表：可能全部为风控拒绝，或 LLM/缓存异常"
        )
    return result


def _apply_analysis_to_item(item: CollectTrendObject, analysis_res: Dict[str, Any]) -> bool:
    """把 LLM 分析结果应用到 item。命中红线/不安全则跳过，返回 False。"""
    if analysis_res.get("risk_category") == "RED_LINE" or not analysis_res.get("is_safe_for_marketing"):
        logger.info(
            "Skipping unsafe content: %s (Reason: %s)",
            item.title, analysis_res.get("risk_category"),
        )
        return False
    item.title = analysis_res.get("title", item.title)
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
    Role: 电商营销风控专家与数据清洗工程师。
    Task: 你将接收一组社媒热点内容列表（包含 id, platform, title, summary, tags；注意：TikTok 的 summary 可能夹杂字幕、视频描述和评论），需过滤噪音，逐一分析每个热点的商业机会与风险。
    Analysis Rules (Apply to EACH item in the list):
    1. 内容提炼:
       - 基于输入内容过滤噪音，重新生成精炼的热点标题 (title) 和 热点摘要 (summary，字数严格控制在 50-200 字)。
       - 提取并输出 3-5 个最核心的标签 (tags)。
    2. 情感分析:
       - Label: 仅限 [正面, 中性, 负面]。如果情绪既不偏正也不偏负，或同时包含强烈的正负面情绪（争议大），均判定为“中性”。
       - Score: -100.0 (极负) 到 100.0 (极正) 的浮点数。
    3. 风控与营销定级 (Risk Category - Priority Logic):
       按以下顺序匹配，命中即止：
       3.1 RED_LINE -> 绝对不可营销 (is_safe_for_marketing=false):
           涉及内容：政治斗争、国家政策批评、地缘冲突、台湾问题、中国政治、霸权、谋杀、恐怖主义、儿童剥削、极端暴力、色情、爱泼斯坦案、重大自然灾害等。
           warning_message：请简述触发红线的具体原因。
       3.2 YELLOW_OPPORTUNITY -> 高商业潜力但需谨慎 (is_safe_for_marketing=true):
           特征：行业质量危机、友商服务投诉、消费者权益纠纷。公众愤怒但渴求市场替代方案。
           warning_message：请提示营销切入时的注意事项（如：切忌拉踩、需强调自身品质等）。
       3.3 GREEN_SAFE -> 低风险，安全可营销 (is_safe_for_marketing=true):
           特征：正面新闻、知识科普、轻松有趣、自嘲式幽默、搞笑梗等。
           warning_message：固定输出“无”。
    4. 受众画像推断 (Audience Inference): 
       推断 3-5 个具体的关注该热点的人群标签 (如："18-35岁职场女性", "硬核科技粉", "母婴成分党")，拒绝使用“网民”、“大众”等泛词。

    Output Format:
    - Strict JSON only. No markdown formatting (do not use ```json), no explanations.
    - The output must be a JSON object containing a "results" key, which is a list of objects corresponding exactly to the input items.
    - Schema for EACH object in "results": 
    {{
       "id": "String (Copy exactly from input 'id')",
       "title": "String (Short display title)",
       "summary": "String (Brief Summary, 50-200 words)", 
       "tags": ["String", "String", ...],
       "sentiment_score": Float (-100.0 to 100.0), 
       "sentiment_label": "String (正面、中性、负面)", 
       "risk_category": "String (Must be one of: RED_LINE, YELLOW_OPPORTUNITY, GREEN_SAFE)", 
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
    """异步调用大模型对热点进行批量情感分析、风险评估和受众推断。"""
    prompt = _build_analysis_prompt(content_list)
    try:
        response = await ASYNC_LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
        )
        _log_token_usage(response.usage if hasattr(response, "usage") else None)
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.exception("Async LLM analysis failed: %s", e)
        return {}


def _map_sentiment(label: str) -> SentimentCN:
    mapping = {
        "正面": SentimentCN.positive,
        "中性": SentimentCN.neutral,
        "负面": SentimentCN.negative,
    }
    return mapping.get(label, SentimentCN.neutral)

import os
import json
import re
import math
from typing import Optional, Iterable, Any, Dict, List

import httpx
import jieba
from openai import OpenAI
from sqlalchemy.orm import Session
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import     cosine_similarity

from app.models import Hotspot
from app.core.config import get_settings
from app.schemas.hotspot import (
    HotspotMatchOptions,
    HotspotMatchRequest,
    HotspotMatchResponse,
    TrendObject,
    BrandObject,
    HotspotEvaluateRequest,
    HotspotEvaluateResponse,
    SentimentCN,
    RecommendationLevel,
    MatchRadar
)

settings = get_settings()

# 你可以用任何兼容 OpenAI API 的后端
LLM_CLIENT = OpenAI(
    base_url=settings.LLM_API_URL,
    api_key=settings.LLM_API_KEY
)
LLM_MODEL="qwen3.5-plus"

# 核心热点匹配度计算函数
def batch_match_hotspot_v2(requests: List[HotspotMatchRequest]) -> List[HotspotMatchResponse]:
    """
    批量热点匹配计算
    - 支持分批处理，每批最多 10 个
    """
    all_responses: list[HotspotMatchResponse] = []
    BATCH_SIZE = 10
    
    for i in range(0, len(requests), BATCH_SIZE):
        batch = requests[i:i + BATCH_SIZE]
        
        # 区分需要使用 LLM 的和不需要的
        llm_batch = []
        rule_batch_indices = []
        
        for idx, req in enumerate(batch):
            opt = req.options or HotspotMatchOptions()
            if opt.use_llm:
                llm_batch.append(req)
            else:
                rule_batch_indices.append(idx)
        
        # 处理不需要 LLM 的（离线规则）
        batch_responses = [None] * len(batch)
        for idx in rule_batch_indices:
            req = batch[idx]
            batch_responses[idx] = _match_with_rules(req.trend, req.brand, req.options or HotspotMatchOptions())
            
        # 批量处理需要 LLM 的
        if llm_batch:
            llm_results = _batch_match_with_llm(llm_batch)
            # 将 LLM 结果填回 batch_responses
            llm_idx = 0
            for idx in range(len(batch)):
                if batch_responses[idx] is None:
                    batch_responses[idx] = llm_results[llm_idx]
                    llm_idx += 1
                    
        all_responses.extend(batch_responses)
        
    return all_responses



# 处理单组数据的业务函数
def match_hotspot_v2(request: HotspotMatchRequest) -> HotspotMatchResponse:
    trend = request.trend
    brand = request.brand
    opt = request.options or HotspotMatchOptions()

    # 如果需要使用LLM，那么久跳转到LLM识别的函数，否则进行离线识别
    if opt.use_llm:
        return _match_with_llm(trend, brand, opt)

    # 离线识别
    return _match_with_rules(trend, brand, opt)
def _batch_match_with_llm(requests: List[HotspotMatchRequest]) -> List[HotspotMatchResponse]:
    """
    批量调用大模型进行匹配度分析
    """
    # 构建批量 Prompt
    items_data = []
    for idx, req in enumerate(requests):
        items_data.append({
            "index": idx,
            "trend": {
                "title": req.trend.title,
                "summary": req.trend.summary,
                "tags": req.trend.tags,
                "audience": req.trend.audience
            },
            "brand": {
                "name": req.brand.name,
                "industry": req.brand.industry,
                "core_value": req.brand.core_value,
                "tone": req.brand.tone,
                "audience": req.brand.audience
            }
        })

    prompt = f"""
你是一个品牌营销专家，点评风格简洁到位。评估以下多个热点与品牌的契合度对，并按指定 JSON 格式输出结果。

你必须逐一评估输入的每一组（trend 和 brand），并严格按以下 JSON 格式输出。不要包含任何额外解释。

Output Format:
- Strict JSON only.
- The output must be a JSON object with a "results" key containing a list of objects.
- Schema for EACH object in "results":
{{
  "index": Integer (Matching the input index),
  "semantic_relevance": Float (0-100),
  "tone_fit": Float (0-100),
  "audience_overlap": Float (0-100),
  "risk_index": Float (0-100),
  "compatibility_score": Float (0-100),
  "recommendation": "String (强烈推荐|推荐|值得尝试|谨慎考虑|不建议|强烈不建议)",
  "suggestion": "String (结合热点的...)",
  "reason": "String (语义相关性高...)"
}}

【待评估数据】
{json.dumps(items_data, ensure_ascii=False)}
"""

    try:
        response = LLM_CLIENT.chat.completions.create(
            model=os.getenv("LLM_MODEL", LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("模型返回内容为空")
            
        result_data = json.loads(content)
        llm_results_list = result_data.get("results", [])
        # 按照 index 排序确保顺序一致
        llm_results_list.sort(key=lambda x: x.get("index", 0))
        
        responses = []
        for res in llm_results_list:
            responses.append(HotspotMatchResponse(
                compatibility_score=float(res["compatibility_score"]),
                recommendation=RecommendationLevel.from_str(res["recommendation"]),
                radar=MatchRadar(
                    semantic_relevance=float(res["semantic_relevance"]),
                    tone_fit=float(res["tone_fit"]),
                    audience_overlap=float(res["audience_overlap"]),
                    risk_index=float(res["risk_index"]),
                ),
                reason=res["reason"],
                suggestion=res["suggestion"],
                risk_warning=None,
            ))
            
        # 如果模型返回的数量不对，或者解析失败，降级处理
        if len(responses) != len(requests):
            print(f"LLM returned {len(responses)} results for {len(requests)} requests. Falling back to rules.")
            return [_match_with_rules(req.trend, req.brand, req.options or HotspotMatchOptions()) for req in requests]
            
        return responses
        
    except Exception as e:
        print(f"Batch LLM match failed: {e}. Falling back to rule-based.")
        return [_match_with_rules(req.trend, req.brand, req.options or HotspotMatchOptions()) for req in requests]

# --------------------------
# Legacy：旧版 一次API只能处理一次请求
# --------------------------
def _match_with_llm(trend: TrendObject, brand: BrandObject, opt: HotspotMatchOptions) -> HotspotMatchResponse:
    prompt = f"""
你是一个品牌营销专家，点评风格简洁到位，评估以下热点与品牌的契合度，并按指定json格式输出结果。

【热点信息】
标题：{trend.title}
摘要：{trend.summary}
标签：{', '.join(trend.tags)}
受众：{', '.join(trend.audience) if trend.audience else '未提供'}

【品牌信息】
名称：{brand.name}
主要售卖产品：{brand.industry}
核心价值：{brand.core_value or '未提供'}
品牌调性：{brand.tone}
目标受众：{', '.join(brand.audience) if brand.audience else '未提供'}

你必须严格按以下 JSON 格式输出，不要包含任何额外解释、前言或后缀，直接输出纯文本的 JSON 字符串。
其中 "recommendation" 字段必须从以下值中选择：["强烈推荐", "推荐", "值得尝试", "谨慎考虑", "不建议", "强烈不建议"]。
JSON 格式示例：
{{
  "semantic_relevance": 85.0,
  "tone_fit": 90.0,
  "audience_overlap": 80.0,
  "risk_index": 10.0,
  "compatibility_score": 84.5,
  "recommendation": "强烈推荐",
  "suggestion": "结合热点的...",
  "reason": "语义相关性高，受众高度重合..."
}}
"""

    try:
        response = LLM_CLIENT.chat.completions.create(
            model=os.getenv("LLM_MODEL", LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # 确保输出稳定
            max_tokens=500,
            response_format={"type": "json_object"}, #强制开启json模式，会屏蔽掉所有非json字符
        )

        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            print("=" * 50)
            print(f"Token消耗详情（{LLM_MODEL}）：")
            print(f"输入Token（Prompt）：{usage.prompt_tokens}")
            print(f"输出Token（Completion）：{usage.completion_tokens}")
            print(f"总消耗Token：{usage.total_tokens}")
            print("=" * 50)
        else:
            print("⚠️  未获取到Token使用信息，可能是API版本或模型不支持")

        content = response.choices[0].message.content
        if not content:
            raise ValueError("模型返回内容为空")
        # 使用稳健的解析函数
        result = parse_llm_json(content)

        # 后续校验关键字段是否存在（防止模型少写字段）
        required_keys = ["compatibility_score", "recommendation", "suggestion", "reason"]
        for key in required_keys:
            if key not in result:
                raise KeyError(f"缺少必要字段: {key}")

        return HotspotMatchResponse(
            compatibility_score=float(result["compatibility_score"]),
            recommendation=RecommendationLevel.from_str(result["recommendation"]),
            radar=MatchRadar(
                semantic_relevance=float(result["semantic_relevance"]),
                tone_fit=float(result["tone_fit"]),
                audience_overlap=float(result["audience_overlap"]),
                risk_index=float(result["risk_index"]),
            ),
            reason=result["reason"],
            suggestion=result["suggestion"],
            risk_warning=None,
        )
    except Exception as e:
        # LLM 失败时降级到传统方法（可选）
        print(f"LLM failed, fallback to rule-based: {e}")
        return _match_with_rules(trend, brand, opt)  # 把你原来的逻辑抽成函数

def parse_llm_json(content:str)-> Dict[str, Any]:
    # 提取 JSON（有些模型会加 ```json...```）
    text = content.strip()

    # 1. 去除 markdown 代码块标记
    if text.startswith("```"):
        # 移除开头的```和可能的json标识
        text = text.lstrip("`")  # 移除开头所有反引号
        if text.lower().startswith("json"):  # 处理```json的情况
            text = text[4:]  # 跳过json这4个字符
        # 移除结尾的```
        text = text.rstrip("`")  # 移除结尾所有反引号
        # 清理空白
        text = text.strip()


    # 2. 防御性提取：找到第一个 { 和最后一个 }
    start_idx = text.find('{')
    end_idx = text.rfind('}')

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_str = text[start_idx: end_idx + 1]
    else:
        # 如果连花括号都找不到，直接报错
        raise ValueError(f"未找到有效的 JSON 结构: {text[:100]}...")

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}. 原始内容片段: {json_str[:200]}")



# --------------------------
# Legacy：旧版 /hotspot/evaluate 所需的计算函数（此前缺失会导致导入失败）
# --------------------------
_CN_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")

def _match_with_rules(trend: TrendObject, brand: BrandObject, opt: HotspotMatchOptions) -> HotspotMatchResponse:
    # Step 1: 风险过滤
    risk, risk_warning = _risk_index(trend)  # 默认5

    # Step 2: 语义相关性（离线向量：TF-IDF）
    trend_text = f"{trend.title}\n{trend.summary}\n{_safe_join(trend.tags)}"
    brand_text = f"{brand.name}\n{brand.industry}\n{brand.core_value or ''}\n{brand.tone}"
    semantic = _tfidf_cosine(trend_text, brand_text) * 100.0

    # Step 3: 调性/创意维度
    tone = _tone_fit(trend, brand)
    creative = _creative_space(trend, brand, semantic)

    # Step 4: 受众重合（Jaccard--直接看有没有重复的字符串，没有词语延伸，可信度低），不足给默认 50
    aud = _jaccard_list(trend.audience, brand.audience) * 100.0
    if (trend.audience is None) or (brand.audience is None):
        # 没画像时，给中性基线，避免直接把总分打穿
        aud = max(aud, 50.0)

    # Step 5: 加权合成（0-100），再做受众系数修正 + 风险惩罚
    w_sum = max(1e-6, opt.w_semantic + opt.w_tone + opt.w_creative)
    base = (semantic * opt.w_semantic + tone * opt.w_tone + creative * opt.w_creative) / w_sum

    # 受众修正：0.85 ~ 1.00
    audience_coef = 0.85 + 0.15 * (aud / 100.0)
    score = base * audience_coef

    # 风险惩罚：risk 15->轻微；risk 90->大幅
    risk_penalty = max(0.0, 1.0 - (risk / 120.0))
    score *= risk_penalty
    score = float(round(max(0.0, min(100.0, score)), 1))

    rec = _recommendation(score, risk)
    suggestion = _one_line_suggestion(trend, brand)

    reason = (
        f"语义相关性 {semantic:.0f}/100，调性匹配 {tone:.0f}/100，创意空间 {creative:.0f}/100；"
        f"受众重合 {aud:.0f}/100，风险指数 {risk:.0f}/100。"
    )

    return HotspotMatchResponse(
        compatibility_score=score,
        recommendation=rec,
        radar=MatchRadar(
            semantic_relevance=float(round(semantic, 1)),
            tone_fit=float(round(tone, 1)),
            audience_overlap=float(round(aud, 1)),
            risk_index=float(round(risk, 1)),
        ),
        suggestion=suggestion,
        reason=reason,
        risk_warning=risk_warning,
    )










def calculate_category_match(merchant_category: str, hotspot_title: str) -> float:
    """
    旧版：品类匹配度（0-1）
    - 逻辑：基于分词后的 token overlap + 少量启发式
    """
    a = set(_tokenize_cn(merchant_category))
    b = set(_tokenize_cn(hotspot_title))
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    j = inter / union if union else 0.0
    # 旧版偏“能不能搭界”，给一点放大
    return float(min(1.0, j * 1.4))


def calculate_keyword_similarity(merchant_keywords: list[str], hotspot_keywords: list[str]) -> float:
    """
    旧版：关键词相似度（0-1），使用 Jaccard
    """
    a = set([k.strip().lower() for k in (merchant_keywords or []) if k and k.strip()])
    b = set([k.strip().lower() for k in (hotspot_keywords or []) if k and k.strip()])
    if not a or not b:
        return 0.0
    return float(len(a & b) / len(a | b))


def generate_analysis(adapt_score: float, category_match: float, keyword_similarity: float) -> str:
    """
    旧版：输出一句话分析（保持调用方兼容）
    """
    if adapt_score >= 80:
        level = "强烈推荐"
    elif adapt_score >= 60:
        level = "值得尝试"
    elif adapt_score >= 40:
        level = "谨慎考虑"
    else:
        level = "不建议"
    return (
        f"{level}：综合适配分 {adapt_score:.1f}。"
        f"品类匹配度 {category_match:.2f}，关键词相似度 {keyword_similarity:.2f}。"
    )


# --------------------------
# V2：热点匹配混合加权算法（离线可运行；可选 LLM 精算）
# --------------------------
_RISK_KEYWORDS = {
    "丑闻", "出轨", "离婚", "塌房", "诈骗", "涉毒", "吸毒", "暴力", "自杀", "灾难", "事故", "爆炸", "死亡",
    "维权", "抵制", "封杀", "侵权", "造假", "翻车", "危机", "投诉", "黑幕",
}


def _safe_join(items: Optional[Iterable[str]], sep: str = " ") -> str:
    if not items:
        return ""
    return sep.join([str(x).strip() for x in items if x and str(x).strip()])


def _tfidf_cosine(a: str, b: str) -> float:
    """
    计算 TF-IDF + 余弦相似度（0-1），使用 sklearn + jieba 分词。
    """
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    def jieba_tokenizer(text: str) -> list[str]:
        return _tokenize_cn(text)

    vec = TfidfVectorizer(tokenizer=jieba_tokenizer, lowercase=False)
    tfidf = vec.fit_transform([a, b])
    sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]
    if math.isnan(sim):
        return 0.0
    return float(max(0.0, min(1.0, sim)))


def _jaccard_list(a: Optional[list[str]], b: Optional[list[str]]) -> float:
    sa = set([str(x).strip().lower() for x in (a or []) if x and str(x).strip()])
    sb = set([str(x).strip().lower() for x in (b or []) if x and str(x).strip()])
    if not sa or not sb:
        return 0.0
    return float(len(sa & sb) / len(sa | sb))


def _risk_index(trend: TrendObject) -> tuple[float, Optional[str]]:
    """
    风险指数（0-100，越高越危险）+ 风险提示
    """
    if trend.sentiment == SentimentCN.negative:
        return 95.0, "负面舆情热点：默认建议不蹭，避免公关风险。"

    text = f"{trend.title} {trend.summary} {_safe_join(trend.tags)}"
    hits = [kw for kw in _RISK_KEYWORDS if kw in text]
    if hits:
        # 命中越多风险越高
        score = min(90.0, 55.0 + 10.0 * len(hits))
        return score, f"可能存在舆论/合规风险（命中：{', '.join(hits[:6])}）。"
    return 5.0, None


def _tone_fit(trend: TrendObject, brand: BrandObject) -> float:
    """
    调性匹配（0-100）：品牌 tone 与热点 tags/summary 的语气线索做轻量匹配
    """
    tone_tokens = set(_tokenize_cn(brand.tone))
    trend_tokens = set(_tokenize_cn(_safe_join(trend.tags) + " " + trend.summary))
    if not tone_tokens or not trend_tokens:
        return 55.0  # 信息不足给中性默认值
    j = len(tone_tokens & trend_tokens) / max(1, len(tone_tokens | trend_tokens))
    return float(max(0.0, min(100.0, 40.0 + 120.0 * j)))


def _creative_space(trend: TrendObject, brand: BrandObject, semantic_relevance: float) -> float:
    """
    创意空间（0-100）：结合“可玩性标签”+ 语义相关性做启发式。
    """
    tags = set([t.strip("#").strip() for t in (trend.tags or []) if t and t.strip()])
    playful = {"跨界", "联名", "反差", "玩梗", "复古", "怀旧", "国潮", "平价", "上新", "限定", "节日"}
    bonus = 0.0
    if tags & playful:
        bonus += 15.0
    # 行业词在热点里出现也加一点（说明能“搭界”）
    if brand.industry and brand.industry in (trend.title + trend.summary):
        bonus += 10.0
    base = 0.55 * semantic_relevance + 20.0
    return float(max(0.0, min(100.0, base + bonus)))


def _recommendation(score: float, risk: float) -> RecommendationLevel:
    if risk >= 85:
        return RecommendationLevel.strong_no
    if risk >= 70:
        return RecommendationLevel.no
    if score >= 80:
        return RecommendationLevel.strong_yes
    if score >= 60:
        return RecommendationLevel.yes
    if score >= 40:
        return RecommendationLevel.cautious
    return RecommendationLevel.no


def _one_line_suggestion(trend: TrendObject, brand: BrandObject) -> str:
    # 优先用标签做可复制的建议
    tag = None
    for t in (trend.tags or []):
        t = str(t).strip().lstrip("#")
        if t:
            tag = t
            break
    if tag:
        return f"结合热点的「{tag}」元素，用品牌「{brand.tone}」口吻做一张主视觉+一句slogan，落到与「{brand.industry}」相关的具体卖点。"
    return f"用品牌「{brand.tone}」口吻复述热点核心点，并把话题自然引到「{brand.industry}」的一个具体场景/痛点上。"

def _tokenize_cn(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # 先用正则粗切，再用 jieba 精切，避免全是标点导致空
    chunks = _CN_WORD_RE.findall(text)
    tokens: list[str] = []
    for c in chunks:
        # jieba 对英文/数字会原样输出；对中文做分词
        tokens.extend([t.strip() for t in jieba.lcut(c) if t.strip()])
    # 简单去掉过短噪声
    return [t for t in tokens if len(t) >= 2 or t.isalnum()]


def list_hotspots(db: Session, skip: int = 0, limit: int = 20):
    """列出全局热点（所有商家共享），按 id 倒序。"""
    return (
        db.query(Hotspot)
        .order_by(Hotspot.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_hotspot(db: Session, hotspot_id: int):
    """按 id 获取单条热点。"""
    return db.query(Hotspot).filter(Hotspot.id == hotspot_id).first()


# async def assess_hotspot_match(request: AssessmentRequest) -> AssessmentResponse:
#     # Prepare payload for AI service
#     payload = request.model_dump()
#
#     # Mocking response if URL is dummy or empty
#     if not settings.AUTODL_SERVICE_URL or "autodl-service-url" in settings.AUTODL_SERVICE_URL:
#          return AssessmentResponse(
#              match_score=85.5,
#              match_reason="The rising trend of 'dishwater coffee' aligns well with your fun and friendly brand tone, offering a playful marketing opportunity.",
#              brand_fit="High",
#              conversion_prediction="Moderate to High",
#              content_suggestion="Create a humorous video comparing your high-quality coffee with the viral trend.",
#              best_timing="Weekdays morning",
#              products_to_promote=["Premium Dark Roast", "Barista Kit"]
#          )
#
#     async with httpx.AsyncClient() as client:
#         response = await client.post(settings.AUTODL_SERVICE_URL, json=payload, timeout=30.0)
#         response.raise_for_status()
#         return AssessmentResponse(**response.json())

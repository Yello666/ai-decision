import os
import json
import re
import math
from typing import Optional, Iterable, Tuple, List

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
    base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    api_key=os.getenv("LLM_API_KEY", "sk-b0fc3528ced64aa4b31eca19eb10fb39"),
)
LLM_MODEL="qwen-plus"


# 核心热点匹配度计算函数
def match_hotspot_v2(request: HotspotMatchRequest) -> HotspotMatchResponse:
    trend = request.trend
    brand = request.brand
    opt = request.options or HotspotMatchOptions()

    # 如果需要使用LLM，那么久跳转到LLM识别的函数，否则进行离线识别
    if opt.use_llm:
        return _match_with_llm(trend, brand, opt)

    # 离线识别
    return _match_with_rules(trend, brand, opt)


def _match_with_llm(trend: TrendObject, brand: BrandObject, opt: HotspotMatchOptions) -> HotspotMatchResponse:
    prompt = f"""
你是一个品牌营销专家，点评风格简洁到位，评估以下热点与品牌的契合度，并按指定json格式输出结果。

【热点信息】
标题：{trend.title}
摘要：{trend.summary}
标签：{', '.join(trend.tags)}
情感倾向：{trend.sentiment}
受众：{', '.join(trend.audience) if trend.audience else '未提供'}

【品牌信息】
名称：{brand.name}
行业：{brand.industry}
核心价值：{brand.core_value or '未提供'}
品牌调性：{brand.tone}
目标受众：{', '.join(brand.audience) if brand.audience else '未提供'}

你必须严格按以下 JSON 格式输出，key对应的value值需要你根据上面的实际情况进行填写。严格按照下面提供的格式，不要包含任何额外内容。
其中 "recommendation" 字段必须从以下值中选择：["强烈推荐", "推荐", "值得尝试", "谨慎考虑", "不建议", "强烈不建议"]。

{{
  "semantic_relevance": 85.0,
  "tone_fit": 90.0,
  "audience_overlap": 80.0,
  "risk_index": 10.0,
  "compatibility_score": 84.5,
  "recommendation": "强烈推荐",
  "suggestion": "结合热点的「多巴胺穿搭」元素...",
  "reason": "语义相关性高，调性一致，受众高度重合..."
}}
"""

    try:
        response = LLM_CLIENT.chat.completions.create(
            model=os.getenv("LLM_MODEL", LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # 确保输出稳定
            max_tokens=500,
        )
        content = response.choices[0].message.content.strip()
        # 提取 JSON（有些模型会加 ```json...```）
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:].strip()
        result = json.loads(content)

        return HotspotMatchResponse(
            compatibility_score=float(result["compatibility_score"]),
            recommendation=RecommendationLevel.from_str(result["recommendation"]),
            radar=MatchRadar(
                semantic_relevance=float(result["semantic_relevance"]),
                tone_fit=float(result["tone_fit"]),
                audience_overlap=float(result["audience_overlap"]),
                risk_index=float(result["risk_index"]),
            ),
            suggestion=result["suggestion"],
            reason=result["reason"],
            risk_warning=None,
        )
    except Exception as e:
        # LLM 失败时降级到传统方法（可选）
        print(f"LLM failed, fallback to rule-based: {e}")
        return _match_with_rules(trend, brand, opt)  # 把你原来的逻辑抽成函数


# --------------------------
# Legacy：旧版 /hotspot/evaluate 所需的计算函数（此前缺失会导致导入失败）
# --------------------------
_CN_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")

def _match_with_rules(trend: TrendObject, brand: BrandObject, opt: HotspotMatchOptions) -> HotspotMatchResponse:
    # Step 1: 风险过滤
    risk, risk_warning = _risk_index(trend)  # 默认5
    if trend.sentiment == SentimentCN.negative:
        return HotspotMatchResponse(
            compatibility_score=0.0,
            recommendation=RecommendationLevel.strong_no,
            radar=MatchRadar(
                semantic_relevance=0.0,
                tone_fit=0.0,
                audience_overlap=0.0,
                risk_index=risk,
            ),
            suggestion="不建议借势负面热点，优先做品牌安全与舆情隔离。",
            reason="热点情感倾向为负面，存在显著公关风险。",
            risk_warning=risk_warning,
        )

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


def list_hotspots(db: Session, shopify_store_id: str, skip: int = 0, limit: int = 20):
    return (
        db.query(Hotspot)
        .filter(Hotspot.shopify_store_id == shopify_store_id)
        .order_by(Hotspot.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_hotspot(db: Session, shopify_store_id: str, hotspot_id: int):
    return (
        db.query(Hotspot)
        .filter(Hotspot.shopify_store_id == shopify_store_id)
        .filter(Hotspot.id == hotspot_id)
        .first()
    )


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

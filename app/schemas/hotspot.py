from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

import math
import re
from typing import Iterable, Tuple

import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# --------------------------
# V2：结构化输入（Trend + Brand）与结构化输出（含雷达维度/建议）
# --------------------------
class SentimentCN(str, Enum): #输入的热点情感只有三个维度
    positive = "正面"
    neutral = "中性"
    negative = "负面"

#没有id和跳转链接
# 输入给大模型作判断的热点，尽量精简减少token消耗
class TrendObject(BaseModel):
    title: str = Field(..., description="热点标题/关键词")
    summary: str = Field(..., min_length=1, description="热点描述/摘要（50-200字建议，但不强制）")
    tags: List[str] = Field(default_factory=list, description="核心标签，如：#美食 #跨界")
    sentiment: SentimentCN = Field(default=SentimentCN.neutral, description="情感倾向：正面/负面/中性")
    audience: Optional[List[str]] = Field(default=None, description="热点受众画像（可选）")
    view_count: int = Field(..., description="播放/浏览量")
    publish_time: str = Field(..., description="发布时间（ISO格式，如：2026-02-12T10:00:00）")

# 爬虫输出的热点，展示到前端
class CollectTrendObject(BaseModel):
    id: str = Field(..., description="热点唯一标识ID")
    title: str = Field(..., description="热点标题/关键词")
    summary: str = Field(..., min_length=1, description="热点描述/摘要（50-200字建议，但不强制）")
    tags: List[str] = Field(default_factory=list, description="核心标签，如：#美食 #跨界")
    sentiment: SentimentCN = Field(default=SentimentCN.neutral, description="情感倾向：正面/负面/中性")
    audience: Optional[List[str]] = Field(default=None, description="热点受众画像（可选）")
    jump_url: str = Field(..., description="跳转链接（视频/文字页面地址）")
    view_count: int = Field(..., description="播放/浏览量")
    publish_time: str = Field(..., description="发布时间（ISO格式，如：2026-02-12T10:00:00）")
    platform: str = Field(..., description="热点搜集平台，如Tiktok")


class BrandObject(BaseModel):
    name: str = Field(..., description="品牌名称")
    core_value: Optional[str] = Field(default=None, description="品牌Slogan/核心价值")
    industry: str = Field(..., description="品牌行业/品类")
    tone: str = Field(..., description="品牌调性（年轻/高端/搞怪/严谨等）")
    audience: Optional[List[str]] = Field(default=None, description="品牌目标受众（可选）")


class MatchRadar(BaseModel):
    semantic_relevance: float = Field(..., ge=0, le=100, description="语义相关性（0-100）")
    tone_fit: float = Field(..., ge=0, le=100, description="调性匹配度（0-100）")
    audience_overlap: float = Field(..., ge=0, le=100, description="受众重合度（0-100）")
    risk_index: float = Field(..., ge=0, le=100, description="风险指数（0-100，越高越危险）")


class RecommendationLevel(str, Enum):
    strong_yes = "强烈推荐"
    yes = "推荐"
    try_it = "值得尝试"
    cautious = "谨慎考虑"
    no = "不建议"
    strong_no = "强烈不建议"

    @classmethod
    def from_str(cls, value: str) -> RecommendationLevel:
        # 常见模型输出变体映射
        mapping = {
            "强烈推荐": cls.strong_yes,
            "推荐": cls.yes,
            "值得尝试": cls.try_it,
            "谨慎考虑": cls.cautious,
            "不建议": cls.no,
            "强烈不建议": cls.strong_no,
            "强烈不推荐": cls.strong_no,  # 修复用户遇到的问题
            "不推荐": cls.no,
        }
        return mapping.get(value, cls.no)


class HotspotMatchOptions(BaseModel):
    """算法选项：默认离线可运行；如配置了 LLM，可打开 use_llm 做精算。"""

    use_llm: bool = Field(default=False, description="是否启用大模型精算（需要配置环境变量）")
    use_embedding_prefilter: bool = Field(default=False, description="是否启用向量粗筛（适合海量热点场景）")
    # 权重（可按业务调整）
    w_semantic: float = Field(default=0.4, ge=0, le=1)
    w_tone: float = Field(default=0.3, ge=0, le=1)
    w_creative: float = Field(default=0.3, ge=0, le=1)


class HotspotMatchRequest(BaseModel):
    trend: TrendObject
    brand: BrandObject
    options: HotspotMatchOptions = Field(default_factory=HotspotMatchOptions)


class HotspotMatchResponse(BaseModel):
    compatibility_score: float = Field(..., ge=0, le=100, description="契合度得分（0-100）")
    recommendation: RecommendationLevel
    radar: MatchRadar
    suggestion: str = Field(..., description="营销切入点建议（一句话）")
    reason: str = Field(..., description="简短分析理由")
    risk_warning: Optional[str] = Field(default=None, description="风险提示（如存在公关风险）")


class ShopInput(BaseModel):
    category: str
    brand_tone: str


class HotspotEvaluateRequest(BaseModel):
    """旧版热点适配评估请求（仅用于兼容历史调用方）。"""

    merchant_category: str = Field(..., description="商家品类（如：女装、家居、3C数码）")
    merchant_keywords: List[str] = Field(default_factory=list, description="商家核心关键词")
    hotspot_title: str = Field(..., description="热点标题")
    hotspot_keywords: List[str] = Field(default_factory=list, description="热点核心关键词")


class HotspotEvaluateResponse(BaseModel):
    """旧版热点适配评估响应（仅用于兼容历史调用方）。"""

    adapt_score: float = Field(..., ge=0, le=100, description="适配分数（0-100）")
    analysis: str = Field(..., description="结果分析")
    category_match: float = Field(..., ge=0, le=1, description="品类匹配度（0-1）")
    keyword_similarity: float = Field(..., ge=0, le=1, description="关键词相似度（0-1）")


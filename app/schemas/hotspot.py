from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator



# --------------------------
# V2：结构化输入（Trend + Brand）与结构化输出（含雷达维度/建议）
# --------------------------
class SentimentCN(str, Enum): #输入的热点情感只有三个维度
    positive = "正面"
    neutral = "中性"
    negative = "负面"

class RiskEnum(str,Enum):
    red="RED_LINE"
    yellow="YELLOW_OPPORTUNITY"
    green="GREEN_SAFE"
    none="NONE"

#没有id和跳转链接
# 输入给大模型作判断的热点，尽量精简减少token消耗
class TrendObject(BaseModel):
    title: str = Field(..., description="热点标题/关键词")
    summary: str = Field(..., min_length=1, description="热点描述/摘要（50-200字建议，但不强制）")
    tags: List[str] = Field(default_factory=list, description="核心标签，如：#美食 #跨界")
    # view_counts: int = Field(..., description="播放/浏览量")
    # likes:int=Field(..., description="点赞数")
    # publish_time: str = Field(..., description="发布时间（ISO格式，如：2026-02-12T10:00:00）")
    # sentiment_label: SentimentCN = Field(default=SentimentCN.neutral, description="情感倾向：正面/负面/中性")
    # 不需要展示sentiment，只会有中性、积极两种，而且模型本来就可以根据文本分析
    audience: Optional[List[str]] = Field(default=None, description="热点受众画像（可选）")
    #需要audience，因为这个比tag要精确很多

# 获取到的输出的热点，展示到前端（全面的信息）
class CollectTrendObject(BaseModel):
    id: str = Field(..., description="热点唯一标识ID")
    title: str = Field(..., description="热点标题/关键词")
    summary: str = Field(..., min_length=1, description="热点描述/摘要（50-200字建议，但不强制）")
    tags: List[str] = Field(default_factory=list, description="核心标签，如：#美食 #跨界")

    # 展示给客户，需要展示情感倾向，这可以提升用户体验，体现分析过程的严谨性，
    sentiment_label: SentimentCN = Field(default=SentimentCN.neutral, description="情感倾向：正面/负面/中性")
    sentiment_score:float=Field(default=0.0, description="情感倾向程度，-100为极度负面，100为极度正面")

    audience: Optional[List[str]] = Field(default=None, description="热点受众画像（可选）")

    risk_category: RiskEnum = Field(default=RiskEnum.none, description="营销风险评估")
    warning_message: str = Field(default="", description="营销风险建议")

    jump_url: str = Field(..., description="跳转链接（视频/文字页面地址）")
    view_count: int = Field(..., description="播放/浏览量")
    likes: int = Field(..., description="点赞数")
    publish_time: str = Field(..., description="发布时间（ISO格式，如：2026-02-12T10:00:00）")
    platform: str = Field(..., description="热点搜集平台，如Youtube")


class BrandObject(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="品牌名称")
    core_value: Optional[str] = Field(default=None, description="品牌Slogan/核心价值/品牌介绍")
    mainly_sold_products: str = Field(...,description="主要售卖商品品类",)
    tone: str = Field(..., description="品牌风格（年轻/高端/搞怪/严谨等）")
    audience: Optional[List[str]] = Field(default=None, description="品牌目标受众（可选）")


class BrandUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(default=None, description="品牌名称")
    core_value: Optional[str] = Field(default=None, description="品牌Slogan/核心价值")
    mainly_sold_products: Optional[str] = Field(
        default=None,
        description="主要售卖商品品类",
        alias="industry",
    )
    tone: Optional[str] = Field(default=None, description="品牌调性（年轻/高端/搞怪/严谨等）")
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


class HotspotLLMModel(str, Enum):
    qwen_36_plus = "qwen3.6-plus"
    qwen_36_flash = "qwen3.6-flash-2026-04-16"


class RecommendEmailScheduleMode(str, Enum):
    interval_from_now = "interval_from_now"
    daily_fixed = "daily_fixed"
    interval_from_fixed = "interval_from_fixed"


class HotspotTrendRequest(BaseModel):
    """获取热点数据的请求参数结构体（分页）"""
    platforms: List[str] = Field(default=["youtube"], description="需要获取热点的平台列表")
    page: int = Field(default=1, ge=1, le=5, description="页码（1-5）")
    page_size: int = Field(default=10, ge=1, le=10, description="每页条数（1-10）")


class PaginatedTrendResponse(BaseModel):
    """分页热点响应"""
    items: List[CollectTrendObject] = Field(description="当前页热点列表")
    total: int = Field(description="热点总条数")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页条数")
    total_pages: int = Field(description="总页数")


class HotspotBatchMatchRequest(BaseModel):
    """批量热点匹配请求：品牌信息由服务端按当前登录商户从 DB 加载，无需前端传入。"""

    trends: List[TrendObject] = Field(..., description="待分析的热点列表")
    llm_model: HotspotLLMModel = Field(
        default=HotspotLLMModel.qwen_36_plus,
        description="匹配分析使用的大模型：qwen3.6-plus 或 qwen3.6-flash",
    )


class HotspotMatchResponse(BaseModel):
    brand_name: str = Field(..., description="品牌名")
    trend_title: str = Field(..., description="热点标题")
    compatibility_score: float = Field(..., ge=0, le=100, description="契合度得分（0-100）")
    recommendation: RecommendationLevel
    radar: MatchRadar
    reason: str = Field(..., description="简短分析理由")
    suggestion: str = Field(..., description="营销切入点建议（一句话）")
    risk_warning: Optional[str] = Field(default=None, description="风险提示（如存在公关风险）")


class HotspotRecommendRequest(BaseModel):
    """推荐热点：对全量缓存列表中全部热点做品牌匹配，再按契合度下限过滤。"""

    min_compatibility_score: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="契合度低于该值的热点不返回",
    )


class HotspotRecommendedItem(BaseModel):
    """单条推荐：完整热点展示信息 + 品牌匹配结果。"""

    trend: CollectTrendObject = Field(..., description="热点展示字段")
    match: HotspotMatchResponse = Field(..., description="与当前商户品牌的匹配结果")


class HotspotRecommendResponse(BaseModel):
    """推荐热点列表响应。"""

    items: List[HotspotRecommendedItem] = Field(default_factory=list, description="通过分数阈值后的热点列表")
    min_compatibility_score: float = Field(..., description="本次请求使用的最低契合度")
    analyzed_count: int = Field(..., description="实际参与品牌匹配的热点条数（全量缓存中的条数）")


class HotspotRecommendEmailScheduleUpsertRequest(BaseModel):
    """开启/更新定时热点推荐邮件：最低契合度 + 整数小时间隔；启用时由服务端记录锚点时间并按间隔触发。"""

    is_enabled: bool = Field(default=True, description="是否启用定时发送")
    mode: RecommendEmailScheduleMode = Field(
        default=RecommendEmailScheduleMode.interval_from_now,
        description="定时模式：interval_from_now|daily_fixed|interval_from_fixed",
    )
    min_compatibility_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="定时任务使用的最低契合度阈值，与 /hotspot/recommend 一致",
    )
    send_hour: int = Field(..., ge=0, le=23, description="每日发送的小时（0-23）")
    send_minute: int = Field(..., ge=0, le=59, description="每日发送的分钟（0-59）")
    timezone: str = Field(
        default="Asia/Shanghai",
        max_length=64,
        description="IANA 时区名，如 Asia/Shanghai、UTC",
    )
    interval_hours: int = Field(
        default=24,
        ge=1,
        le=8760,
        description="两次发送之间的最短间隔（整数小时）；interval_from_now/interval_from_fixed 模式必填",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"无效的时区: {v}") from e
        return v

    @field_validator("interval_hours")
    @classmethod
    def validate_interval_hours(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval_hours 必须 >= 1")
        return v

    @model_validator(mode="after")
    def validate_by_mode(self):
        if self.mode == RecommendEmailScheduleMode.daily_fixed:
            return self
        if self.mode in (
            RecommendEmailScheduleMode.interval_from_now,
            RecommendEmailScheduleMode.interval_from_fixed,
        ) and self.interval_hours < 1:
            raise ValueError("当前 mode 需要 interval_hours >= 1")
        return self


class HotspotRecommendEmailScheduleResponse(BaseModel):
    """当前商户的热点推荐邮件定时配置。"""

    id: int = Field(..., description="配置主键")
    merchant_id: int = Field(..., description="商户 id")
    is_enabled: bool = Field(..., description="是否启用")
    mode: RecommendEmailScheduleMode = Field(..., description="定时模式")
    min_compatibility_score: float = Field(..., description="最低契合度")
    send_hour: int = Field(..., description="每日发送小时")
    send_minute: int = Field(..., description="每日发送分钟")
    timezone: str = Field(..., description="IANA 时区")
    interval_hours: int = Field(..., description="最短发送间隔（小时）")
    last_sent_at: Optional[datetime] = Field(default=None, description="上次成功发送时间（UTC）")
    last_triggered_at: Optional[datetime] = Field(default=None, description="上次触发尝试时间（UTC）")


class HotspotRecommendEmailScheduleStateResponse(BaseModel):
    """
    前端读取定时配置：未在库中保存过任何一行时 `configured=false`，
    其它字段为表单可用的默认值（`min_compatibility_score` 会尽量采用 Redis 中推荐偏好）。
    """

    configured: bool = Field(..., description="是否已在数据库保存过定时配置")
    id: Optional[int] = Field(default=None, description="配置主键；未保存过则为 null")
    merchant_id: int = Field(..., description="商户 id")
    is_enabled: bool = Field(..., description="是否启用定时发送")
    mode: RecommendEmailScheduleMode = Field(..., description="定时模式")
    min_compatibility_score: float = Field(..., description="最低契合度")
    send_hour: int = Field(..., description="每日发送小时")
    send_minute: int = Field(..., description="每日发送分钟")
    timezone: str = Field(..., description="IANA 时区")
    interval_hours: int = Field(..., description="最短发送间隔（小时）")
    last_sent_at: Optional[datetime] = Field(default=None, description="上次成功发送时间（UTC）")
    last_triggered_at: Optional[datetime] = Field(default=None, description="上次触发尝试时间（UTC）")


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


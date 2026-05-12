from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.hotspot import HotspotMatchResponse


class OwnHotspotCreate(BaseModel):
    """新建用户自有热点（请求体）。tags / audience 可选。"""

    title: str = Field(..., min_length=1, max_length=255)
    summary: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    audience: List[str] = Field(default_factory=list)


class OwnHotspotUpdate(BaseModel):
    """更新用户自有热点：未传字段不改；传空列表表示清空。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    summary: Optional[str] = Field(default=None, min_length=1)
    tags: Optional[List[str]] = None
    audience: Optional[List[str]] = None


class OwnHotspotOut(BaseModel):
    """单条用户自有热点返回。"""

    id: int
    merchant_id: int
    title: str
    summary: str
    tags: List[str] = Field(default_factory=list)
    audience: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class OwnHotspotRecommendRequest(BaseModel):
    """推荐用户自有热点：对当前商户全部热点做品牌匹配后按契合度过滤。"""

    min_compatibility_score: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="契合度低于该值的热点不返回",
    )


class OwnHotspotRecommendedItem(BaseModel):
    """单条推荐：用户热点完整字段 + 品牌匹配结果。"""

    hotspot: OwnHotspotOut = Field(..., description="用户自上传热点")
    match: HotspotMatchResponse = Field(..., description="与当前商户品牌的匹配结果")


class OwnHotspotRecommendResponse(BaseModel):
    """推荐用户热点列表响应。"""

    items: List[OwnHotspotRecommendedItem] = Field(
        default_factory=list,
        description="通过分数阈值后的热点列表",
    )
    min_compatibility_score: float = Field(..., description="本次请求使用的最低契合度")
    analyzed_count: int = Field(..., description="实际参与品牌匹配的热点条数（该商户全部热点）")

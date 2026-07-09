from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


LensType = Literal["all", "products", "visual_matches", "exact_matches"]
ProfileSource = Literal["ai", "match", "manual"]
ProfileStatus = Literal["draft", "confirmed"]
WeightUnit = Literal["g", "kg", "lb", "oz"]


class InstagramRunRequest(BaseModel):
    """运行 Instagram 名人监控：抓最新帖子、下载图片并识图。"""

    profiles: list[str] | None = Field(
        default=None,
        description="要监控的 Instagram 账号；不传则使用 config.INSTAGRAM_PROFILES",
    )
    posts_per_profile: int = Field(default=3, ge=1, le=20, description="每个账号抓取最新帖子数")
    max_images_per_post: int = Field(default=4, ge=1, le=10, description="每条帖子最多处理图片数")


class InstagramRunResponse(BaseModel):
    profiles: list[str]
    fetched_posts: int
    processed_posts: int
    skipped_posts: int
    failed_posts: int
    object_total: int
    output_dir: str


class MonitorCreateRequest(BaseModel):
    platform: str = Field(default="instagram", description="平台：instagram/youtube 等")
    handle: str = Field(..., min_length=1, description="账号/频道 handle")
    display_name: str | None = Field(default=None, description="可读名称")
    monitor_type: str = Field(default="profile", description="profile/channel/keyword/hashtag")
    score: float = Field(default=5.0, ge=0.0, le=10.0, description="监控对象评分，默认 5 分")
    is_enabled: bool = Field(default=True, description="是否启用")


class MonitorUpdateRequest(BaseModel):
    display_name: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=10.0)
    is_enabled: bool | None = None


class MonitorOut(BaseModel):
    id: int
    platform: str
    handle: str
    display_name: str | None
    monitor_type: str
    score: float
    is_enabled: bool
    last_checked_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MonitorListResponse(BaseModel):
    items: list[MonitorOut]
    returned_count: int


class MonitorRunRequest(BaseModel):
    """按监控池对象运行监控任务。"""

    monitor_ids: list[int] = Field(..., min_length=1, description="本次要运行的监控对象 ID")
    posts_per_profile: int = Field(default=3, ge=1, le=20, description="每个账号抓取最新帖子数")
    max_images_per_post: int = Field(default=4, ge=1, le=10, description="每条帖子最多处理图片数")


class MonitorRunResponse(InstagramRunResponse):
    unsupported_monitors: list[MonitorOut] = Field(default_factory=list)


class ProductMatchRefreshRequest(BaseModel):
    """对单个商品机会刷新商品匹配。"""

    lens_type: LensType = Field(default="products", description="Google Lens type 参数")
    limit: int = Field(default=4, ge=1, le=20, description="返回前 N 个匹配")


class ProductMatchResponse(BaseModel):
    object: dict[str, Any]
    top_matches: list[dict[str, Any]]
    matched_count: int
    from_cache: bool


class ProductSelectObjectListResponse(BaseModel):
    items: list[dict[str, Any]]
    returned_count: int


class ProductSelectMatchListResponse(BaseModel):
    items: list[dict[str, Any]]
    returned_count: int


def _validate_price_range(min_value: float | None, max_value: float | None, label: str) -> None:
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError(f"{label} 下限不能大于上限")


class ObjectProfileBase(BaseModel):
    cost_price_min: float | None = Field(default=None, ge=0, description="预测采购成本下限")
    cost_price_max: float | None = Field(default=None, ge=0, description="预测采购成本上限")
    selling_price_min: float | None = Field(default=None, ge=0, description="预测售价下限")
    selling_price_max: float | None = Field(default=None, ge=0, description="预测售价上限")
    currency: str | None = Field(default="USD", max_length=16, description="价格币种")
    length_cm: float | None = Field(default=None, ge=0, description="长（cm）")
    width_cm: float | None = Field(default=None, ge=0, description="宽（cm）")
    height_cm: float | None = Field(default=None, ge=0, description="高（cm）")
    volume_cm3: float | None = Field(default=None, ge=0, description="体积（cm³）")
    weight_value: float | None = Field(default=None, ge=0, description="重量数值")
    weight_unit: WeightUnit | None = Field(default=None, description="重量单位")
    source: ProfileSource = Field(default="ai", description="预测来源")
    status: ProfileStatus = Field(default="draft", description="draft|confirmed")
    reference_match_id: int | None = Field(default=None, description="参考相似商品 id")
    notes: str | None = Field(default=None, description="预测依据/备注")

    @model_validator(mode="after")
    def _validate_ranges(self) -> "ObjectProfileBase":
        _validate_price_range(self.cost_price_min, self.cost_price_max, "采购成本")
        _validate_price_range(self.selling_price_min, self.selling_price_max, "售价")
        if self.weight_value is not None and self.weight_unit is None:
            raise ValueError("填写重量时必须指定 weight_unit")
        return self


class ObjectProfileCreateRequest(ObjectProfileBase):
    """创建或覆盖商品机会当前规划。"""


class ObjectProfileUpdateRequest(BaseModel):
    cost_price_min: float | None = Field(default=None, ge=0)
    cost_price_max: float | None = Field(default=None, ge=0)
    selling_price_min: float | None = Field(default=None, ge=0)
    selling_price_max: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=16)
    length_cm: float | None = Field(default=None, ge=0)
    width_cm: float | None = Field(default=None, ge=0)
    height_cm: float | None = Field(default=None, ge=0)
    volume_cm3: float | None = Field(default=None, ge=0)
    weight_value: float | None = Field(default=None, ge=0)
    weight_unit: WeightUnit | None = None
    source: ProfileSource | None = None
    status: ProfileStatus | None = None
    reference_match_id: int | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_ranges(self) -> "ObjectProfileUpdateRequest":
        _validate_price_range(self.cost_price_min, self.cost_price_max, "采购成本")
        _validate_price_range(self.selling_price_min, self.selling_price_max, "售价")
        return self


class ObjectProfileOut(BaseModel):
    id: int
    object_id: int
    cost_price_min: float | None = None
    cost_price_max: float | None = None
    selling_price_min: float | None = None
    selling_price_max: float | None = None
    currency: str | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None
    weight_value: float | None = None
    weight_unit: str | None = None
    source: str
    status: str
    reference_match_id: int | None = None
    notes: str | None = None
    confidence_score: float | None = None
    estimate_detail: dict[str, Any] | None = None
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


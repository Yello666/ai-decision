from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


LensType = Literal["all", "products", "visual_matches", "exact_matches"]


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
    limit: int = Field(default=3, ge=1, le=20, description="返回前 N 个匹配")


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


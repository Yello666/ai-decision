from datetime import datetime
from typing import Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.schemas.hotspot import BrandObject, TrendObject
from app.schemas.product import ProductObject


class ConfigParamsInput(BaseModel):
    # 设为 Optional + default=None，便于 partial update 时通过 exclude_none
    # 仅序列化用户显式传入的字段，避免默认值误覆盖已有 state。
    resolution: Optional[Literal["480p", "720p"]] = None
    ratio: Optional[
        Literal["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
    ] = None
    language: Optional[Literal["zh", "en"]] = None
    watermark: Optional[bool] = None
    generate_audio: Optional[bool] = None


class MediaAssetsInput(BaseModel):
    """图生视频 / 首尾帧插帧等所需的媒体 URL（与 OpenAPI Schema example 一致）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ref_image_urls": ["https://example.com/a.jpg"],
                "first_frame_url": "https://example.com/first.jpg",
                "last_frame_url": "https://example.com/last.jpg",
            }
        }
    )

    ref_image_urls: list[AnyHttpUrl] = Field(
        default_factory=list,
        description="参考图 URL 列表；image_to_video 时建议 1～4 张，纯文生视频可为空列表",
    )
    first_frame_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="首帧图 URL（frame_interpolation 等需要首尾帧时使用）",
    )
    last_frame_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="尾帧图 URL（frame_interpolation 等需要首尾帧时使用）",
    )


class CreateThreadRequest(BaseModel):
    trend: TrendObject = Field(..., description="TrendObject 热点信息")
    brand: BrandObject = Field(..., description="BrandObject 品牌信息")
    product: ProductObject = Field(..., description="ProductObject 产品信息")
    user_input: str = Field(default="", description="用户对视频的想法/要求")
    generation_mode: Literal["text_to_video", "image_to_video", "frame_interpolation"] = "text_to_video"
    media_assets: Optional[MediaAssetsInput] = Field(
        default=None,
        description=(
            "可选。媒体素材：ref_image_urls 为参考图；first_frame_url / last_frame_url 用于首尾帧。"
            "完整示例见 MediaAssetsInput 的 Schema example。"
        ),
    )
    config_params: Optional[ConfigParamsInput] = None


class EditedSegmentInput(BaseModel):
    segment_id: int
    description: Optional[str] = None
    description_zh: Optional[str] = None
    duration: Optional[int] = Field(default=None, ge=4, le=12)
    mode: Optional[Literal["text_to_video", "image_to_video", "frame_interpolation"]] = None


class ResumeThreadRequest(BaseModel):
    action: Literal["approve", "edit", "feedback"]
    edited_segments: list[EditedSegmentInput] = Field(default_factory=list)
    feedback: str = ""

    # 可选：在恢复 Graph 前，顺带覆盖全局视频参数。
    # 任何一项不传则保留当前 state 的原值；传入时执行 merge（config_params）或整体替换（media_assets / generation_mode）。
    config_params: Optional[ConfigParamsInput] = Field(
        default=None,
        description="可选的全局视频参数覆盖，仅覆盖显式传入的字段",
    )
    media_assets: Optional[MediaAssetsInput] = Field(
        default=None,
        description="可选的媒体素材整体替换（传入即整体覆盖 state 中的 media_assets）",
    )
    generation_mode: Optional[
        Literal["text_to_video", "image_to_video", "frame_interpolation"]
    ] = Field(
        default=None,
        description="可选的生成模式切换；切到 image_to_video / frame_interpolation 时需同时提供含图的 media_assets",
    )


class UpdateThreadParamsRequest(BaseModel):
    """仅修改视频全局参数、不触发 Graph 推进。

    与 ResumeThreadRequest 共享参数覆盖字段，但不包含 action/edited_segments/feedback。
    """

    config_params: Optional[ConfigParamsInput] = None
    media_assets: Optional[MediaAssetsInput] = None
    generation_mode: Optional[
        Literal["text_to_video", "image_to_video", "frame_interpolation"]
    ] = None


# ─────────────────────────────────────────────────────────────
# 历史会话（thread）列表
# ─────────────────────────────────────────────────────────────
VideoThreadStatus = Literal["running", "waiting_human", "finished", "error"]


class VideoThreadListItem(BaseModel):
    """列表项：轻量索引字段，不含 segments / 完整 state（那些由 /state 接口按需拉）。"""

    model_config = ConfigDict(from_attributes=True)

    thread_id: str
    status: VideoThreadStatus
    current_step: Optional[str] = None
    title: Optional[str] = None
    # 列表页封面：创建时从 product.image_url / media_assets 落库，之后不变
    thumbnail_url: Optional[str] = None
    # 列表页展示用的最终视频 URL 列表：按 generations.created_at 升序，
    # 仅包含状态为 succeeded 且存在 result_url 的记录。
    # 对应多段视频 thread（多个 segment → 多个 generation），None 代表尚未生成或未成功。
    final_video_urls: list[str] = Field(default_factory=list)
    revision_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class VideoThreadListResponse(BaseModel):
    """分页响应。"""

    items: list[VideoThreadListItem]
    total: int
    limit: int
    offset: int


# ─────────────────────────────────────────────────────────────
# 历史会话详情（回放 plan_script / 用户决策 / revise 轮次）
# ─────────────────────────────────────────────────────────────
# 每个 turn 是 graph state 的一个有语义的节点快照：
#   user_input         → 用户最初的想法（thread 创建时）
#   assistant_draft    → LLM 给出的一版分镜（plan_script / apply_edit / revise_script 产出）
#   user_action        → 用户对某一版草稿做的决策（approve / edit / feedback）
#   submitted          → 进入 Seedance 任务提交，后续不再需要草稿级 replay
TurnKind = Literal["user_input", "assistant_draft", "user_action", "submitted"]
UserActionKind = Literal["approve", "edit", "feedback"]


class ThreadHistoryTurn(BaseModel):
    """单条历史事件（用于前端时间线 / 回放）。"""

    kind: TurnKind
    checkpoint_id: Optional[str] = Field(
        default=None, description="LangGraph checkpoint UUIDv6，用于调试/定位"
    )
    created_at: Optional[datetime] = Field(
        default=None, description="checkpoint 产生时间（取自 UUIDv6 时间戳）"
    )
    step: Optional[str] = Field(
        default=None, description="当时的 current_step，仅用于前端调试"
    )
    revision_count: Optional[int] = Field(
        default=None, description="当时的改写轮次；assistant_draft 可由此识别是第几版草稿"
    )

    # user_input
    user_input: Optional[str] = None

    # assistant_draft：已做语言脱敏的分镜数组
    segments: Optional[list[dict]] = None
    total_duration: Optional[int] = None
    execution_strategy: Optional[Literal["parallel", "sequential"]] = None

    # user_action
    action: Optional[UserActionKind] = None
    human_feedback: Optional[str] = None
    human_edited_segments: Optional[list[dict]] = None


class ThreadHistoryResponse(BaseModel):
    """单个 thread 的历史会话。按时间正序排列，前端直接渲染时间线。"""

    thread_id: str
    status: VideoThreadStatus
    current_step: Optional[str] = None
    revision_count: Optional[int] = None
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    final_video_urls: list[str] = Field(default_factory=list)
    turns: list[ThreadHistoryTurn]

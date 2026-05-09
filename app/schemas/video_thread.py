from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.schemas.hotspot import BrandObject, TrendObject
from app.schemas.product import ProductObject


class ConfigParamsInput(BaseModel):
    # 设为 Optional + default=None，便于 partial update 时通过 exclude_none
    # 仅序列化用户显式传入的字段，避免默认值误覆盖已有 state。
    resolution: Optional[Literal["480p", "720p","1080p"]] = None
    ratio: Optional[
        Literal["16:9", "9:16", "1:1", "3:4", "4:3", "21:9", "adaptive"]
    ] = None
    language: Optional[Literal["zh", "en"]] = None
    watermark: Optional[bool] = None
    generate_audio: Optional[bool] = None


class MediaAssetsInput(BaseModel):
    """Seedance 2.0 多模态参考生成所需的媒体 URL。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ref_image_urls": ["https://example.com/a.jpg"],
                "reference_video_urls": ["https://example.com/style.mp4"],
                "reference_audio_urls": ["https://example.com/voice.mp3"],
            }
        }
    )

    ref_image_urls: list[AnyHttpUrl] = Field(
        default_factory=list,
        description="参考图 URL 列表；multimodal_reference 时最多 9 张，纯文生视频可为空列表",
    )
    reference_video_urls: list[AnyHttpUrl] = Field(
        default_factory=list,
        description="参考视频 URL 列表；multimodal_reference 时最多 3 个",
    )
    reference_audio_urls: list[AnyHttpUrl] = Field(
        default_factory=list,
        description="参考音频 URL 列表；multimodal_reference 时最多 3 段，且不能作为唯一参考素材",
    )


class ProductForPrompt(BaseModel):
    """传给 LLM 剧情规划的最小商品上下文。"""

    name: str = Field(..., description="用于 prompt 的商品或规格名称")
    description: str = Field(..., description="用于 prompt 的商品描述")
    size_description: str = Field(default="", description="商品尺寸描述（如长宽高）")
    price: float = Field(..., description="用于 prompt 的商品或规格价格")
    image_url: str = Field(default="", description="用于 prompt 的商品或规格参考图 URL")


class CreateThreadRequest(BaseModel):
    trend: TrendObject = Field(..., description="TrendObject 热点信息")
    brand: Optional[BrandObject] = Field(default=None, description="BrandObject 品牌信息(可选)")
    product: ProductObject = Field(..., description="ProductObject 产品信息")
    user_input: str = Field(default="", description="用户对视频的想法/要求")
    generation_mode: Literal["text_to_video", "multimodal_reference"] = "text_to_video"
    media_assets: Optional[MediaAssetsInput] = Field(
        default=None,
        description=(
            "可选。媒体素材统一使用 ref_image_urls 传图；首帧、尾帧、参考图身份由提示词中的图序说明。"
            "完整示例见 MediaAssetsInput 的 Schema example。"
        ),
    )
    config_params: Optional[ConfigParamsInput] = None


class EditedSegmentInput(BaseModel):
    segment_id: int
    description: Optional[str] = None
    description_zh: Optional[str] = None
    duration: Optional[int] = Field(
        default=None,
        description="秒；4~15，或 -1 表示由模型自主选择时长",
    )
    mode: Optional[Literal["text_to_video", "multimodal_reference", "first_frame"]] = None

    @field_validator("duration")
    @classmethod
    def _duration_allow_auto(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v == -1:
            return v
        if 4 <= v <= 15:
            return v
        raise ValueError("duration 须为 -1 或 4~15 的整数")


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
        Literal["text_to_video", "multimodal_reference"]
    ] = Field(
        default=None,
        description="可选的生成模式切换；切到 multimodal_reference 时需同时提供图片或视频参考素材",
    )


class UpdateThreadParamsRequest(BaseModel):
    """仅修改视频全局参数、不触发 Graph 推进。

    与 ResumeThreadRequest 共享参数覆盖字段，但不包含 action/edited_segments/feedback。
    """

    config_params: Optional[ConfigParamsInput] = None
    media_assets: Optional[MediaAssetsInput] = None
    generation_mode: Optional[
        Literal["text_to_video", "multimodal_reference"]
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
    product: Optional[dict[str, Any]] = Field(
        default=None,
        description="创建 thread 时写入 graph state 的完整商品快照（与 CreateThreadRequest.product 同源）",
    )
    product_for_prompt: Optional[dict[str, Any]] = Field(
        default=None,
        description="剧情规划/提交侧使用的精简商品上下文（与 ProductForPrompt 同源）",
    )

# ---------- 查询任务响应 ----------
class VideoTaskContent(BaseModel):
    video_url: Optional[str] = Field(default=None, description="生成的视频 URL")
    last_frame_url: Optional[str] = Field(default=None, description="视频尾帧图片 URL（return_last_frame=true 时返回）")


class VideoTaskUsage(BaseModel):
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class VideoTaskError(BaseModel):
    code: Optional[str] = None
    message: Optional[str] = None

class VideoTaskCallbackRequest(BaseModel):
    """方舟平台回调请求体，与查询任务 API 返回体结构一致"""
    id: str = Field(..., description="任务 ID，如 cgt-20250918170228-dw9rb")
    model: Optional[str] = Field(default=None, description="模型名称-版本")
    status: str = Field(
        ...,
        description="任务状态: queued / running / succeeded / failed / expired",
    )
    created_at: Optional[int] = Field(default=None, description="创建时间 (Unix 时间戳)")
    updated_at: Optional[int] = Field(default=None, description="更新时间 (Unix 时间戳)")
    content: Optional[VideoTaskContent] = None
    usage: Optional[VideoTaskUsage] = None
    error: Optional[VideoTaskError] = None
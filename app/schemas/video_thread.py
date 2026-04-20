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

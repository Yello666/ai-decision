from typing import Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.schemas.hotspot import BrandObject, TrendObject
from app.schemas.product import ProductObject


class ConfigParamsInput(BaseModel):
    resolution: Literal["480p", "720p"] = "720p"
    ratio: Literal["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"] = "adaptive"
    language: Literal["zh", "en"] = "zh"
    watermark: bool = False
    generate_audio: bool = True


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

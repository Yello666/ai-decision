"""
Seedance 2.0 视频生成 — Pydantic Schema 定义。

严格对齐火山引擎方舟官方 API 字段名、大小写、嵌套层级、枚举值、数据类型。
官方文档: https://www.volcengine.com/docs/82379/1520757

支持的生成模式（互斥）:
  - text_to_video:          纯文生视频
  - first_frame:            首帧图生视频
  - first_last_frame:       首尾帧图生视频
  - multimodal_reference:   多模态参考生视频（0~9 图 + 0~3 视频 + 0~3 音频）
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ====================================================================
# 官方固定 Content Item JSON 结构
# 禁止修改 type / role / 嵌套层级，字段名与官方一字不差
# ====================================================================

class Seedance2TextContentItem(BaseModel):
    """content 数组 — 文本项"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="输入给模型的文本内容，描述期望生成的视频")


class _ImageUrlObj(BaseModel):
    url: str = Field(..., description="图片 URL 地址（支持 HTTPS 链接）")


class Seedance2ImageContentItem(BaseModel):
    """
    content 数组 — 图片项。

    role 枚举:
      - reference_image: 参考图
      - first_frame:     首帧图
      - last_frame:      尾帧图
    """
    type: Literal["image_url"] = "image_url"
    image_url: _ImageUrlObj
    role: Literal["reference_image", "first_frame", "last_frame"]


class _VideoUrlObj(BaseModel):
    url: str = Field(..., description="视频 URL 地址")


class Seedance2VideoContentItem(BaseModel):
    """content 数组 — 视频项（role 固定为 reference_video）"""
    type: Literal["video_url"] = "video_url"
    video_url: _VideoUrlObj
    role: Literal["reference_video"] = "reference_video"


class _AudioUrlObj(BaseModel):
    url: str = Field(..., description="音频 URL 地址")


class Seedance2AudioContentItem(BaseModel):
    """content 数组 — 音频项（role 固定为 reference_audio）"""
    type: Literal["audio_url"] = "audio_url"
    audio_url: _AudioUrlObj
    role: Literal["reference_audio"] = "reference_audio"


Seedance2ContentItem = Union[
    Seedance2TextContentItem,
    Seedance2ImageContentItem,
    Seedance2VideoContentItem,
    Seedance2AudioContentItem,
]


# ====================================================================
# POST /generations/seedance2/video — 请求体
# ====================================================================

class Seedance2VideoRequest(BaseModel):
    """
    Seedance 2.0 视频生成请求。

    首尾帧模式 (first_frame / first_last_frame) 与纯参考模式
    (multimodal_reference) 互斥，不可混用。
    """

    # ---- 生成模式 ----
    mode: Literal[
        "text_to_video",
        "first_frame",
        "first_last_frame",
        "multimodal_reference",
    ] = Field(
        ...,
        description=(
            "生成模式: "
            "text_to_video=纯文生视频 | "
            "first_frame=首帧图生视频 | "
            "first_last_frame=首尾帧图生视频 | "
            "multimodal_reference=多模态参考生视频"
        ),
    )

    # ---- 提示词（必填）----
    prompt: str = Field(
        ...,
        min_length=1,
        description=(
            "视频生成提示词。多模态参考模式下使用 "
            "[图1][图2]...[音频1][视频1] 格式引用素材，"
            "在提示词内明确说明每个素材用途、动作、画面逻辑"
        ),
    )

    # ---- 首帧/尾帧（仅 first_frame / first_last_frame 模式）----
    first_frame_url: Optional[str] = Field(
        default=None,
        description="首帧图片 URL（first_frame / first_last_frame 模式必填）",
    )
    last_frame_url: Optional[str] = Field(
        default=None,
        description="尾帧图片 URL（first_last_frame 模式必填）",
    )

    # ---- 多模态参考素材（仅 multimodal_reference 模式）----
    reference_image_urls: List[str] = Field(
        default_factory=list,
        description="参考图片 URL 列表，0~9 张",
    )
    reference_video_urls: List[str] = Field(
        default_factory=list,
        description="参考视频 URL 列表，0~3 个",
    )
    reference_audio_urls: List[str] = Field(
        default_factory=list,
        description="参考音频 URL 列表，0~3 段；不可单独传入，必须同时包含图片或视频",
    )

    # ---- 视频生成参数（严格对齐官方枚举、默认值、取值范围）----
    ratio: Optional[Literal[
        "16:9", "9:16", "1:1", "3:4", "4:3", "21:9", "adaptive",
    ]] = Field(
        default="16:9",
        description="视频宽高比。枚举: 16:9 / 9:16 / 1:1 / 3:4 / 4:3 / 21:9 / adaptive",
    )

    duration: Optional[int] = Field(
        default=5,
        ge=4,
        le=15,
        description="视频时长（秒），官方范围 4~15，-1 表示模型自主选择",
    )

    resolution: Optional[Literal["480p", "720p"]] = Field(
        default="720p",
        description="视频分辨率。枚举: 480p / 720p",
    )

    watermark: Optional[bool] = Field(
        default=False,
        description="是否添加水印，boolean 类型",
    )

    generate_audio: Optional[bool] = Field(
        default=True,
        description="是否生成音频。true=有声视频，false=无声视频",
    )

    seed: Optional[int] = Field(
        default=None,
        description="随机种子（integer），-1 为随机",
    )

    # ---- 高级参数 ----
    callback_url: Optional[str] = Field(
        default=None,
        description="任务结果回调通知地址（string）",
    )

    return_last_frame: Optional[bool] = Field(
        default=None,
        description="是否返回尾帧图像（boolean）",
    )

    execution_expires_after: Optional[int] = Field(
        default=None,
        description="任务超时阈值（integer，单位秒），默认 172800",
    )

    tools: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="扩展工具配置数组（如联网搜索等）",
    )

    safety_identifier: Optional[str] = Field(
        default=None,
        description="安全合规标识符（string）",
    )

    @model_validator(mode="after")
    def _validate_mode_constraints(self) -> "Seedance2VideoRequest":
        """校验生成模式与素材参数的互斥约束。"""
        has_ref = (
            bool(self.reference_image_urls)
            or bool(self.reference_video_urls)
            or bool(self.reference_audio_urls)
        )
        has_frame = bool(self.first_frame_url) or bool(self.last_frame_url)

        if self.mode == "text_to_video":
            if has_frame:
                raise ValueError("text_to_video 模式不需要首帧/尾帧参数")
            if has_ref:
                raise ValueError("text_to_video 模式不需要多模态参考素材")

        elif self.mode == "first_frame":
            if not self.first_frame_url:
                raise ValueError("first_frame 模式下 first_frame_url 必填")
            if has_ref:
                raise ValueError("首帧模式与多模态参考模式互斥，不可混用")

        elif self.mode == "first_last_frame":
            if not self.first_frame_url or not self.last_frame_url:
                raise ValueError(
                    "first_last_frame 模式下 first_frame_url 和 last_frame_url 均必填"
                )
            if has_ref:
                raise ValueError("首尾帧模式与多模态参考模式互斥，不可混用")

        elif self.mode == "multimodal_reference":
            if has_frame:
                raise ValueError("多模态参考模式与首帧/尾帧模式互斥，不可混用")
            has_image_or_video = (
                bool(self.reference_image_urls) or bool(self.reference_video_urls)
            )
            if not has_image_or_video:
                raise ValueError(
                    "multimodal_reference 模式至少需要包含图片或视频"
                )
            if (
                self.reference_audio_urls
                and not has_image_or_video
            ):
                raise ValueError("不可单独传入音频，必须至少包含图片或视频")
            if len(self.reference_image_urls) > 9:
                raise ValueError("参考图片数量不可超过 9 张")
            if len(self.reference_video_urls) > 3:
                raise ValueError("参考视频数量不可超过 3 个")
            if len(self.reference_audio_urls) > 3:
                raise ValueError("参考音频数量不可超过 3 段")

        return self


# ====================================================================
# 响应体 — 与官方 API 返回结构对齐
# ====================================================================

class Seedance2TaskContent(BaseModel):
    """任务成功后返回的内容"""
    video_url: Optional[str] = Field(
        default=None, description="生成的视频 URL（24h 有效，请及时转存）",
    )
    last_frame_url: Optional[str] = Field(
        default=None,
        description="视频尾帧图片 URL（return_last_frame=true 时返回）",
    )


class Seedance2TaskUsage(BaseModel):
    """Token 用量"""
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class Seedance2TaskError(BaseModel):
    """错误详情"""
    code: Optional[str] = None
    message: Optional[str] = None


class Seedance2CreateTaskResponse(BaseModel):
    """创建视频生成任务的响应"""
    id: str = Field(..., description="任务 ID，如 cgt-20260410125521-rpf27")
    model: Optional[str] = Field(default=None, description="使用的模型")
    status: str = Field(
        ...,
        description="任务状态，提交成功时通常为 submitted",
    )
    created_at: Optional[int] = Field(
        default=None, description="创建时间 (Unix 时间戳)",
    )


class Seedance2TaskStatusResponse(BaseModel):
    """
    查询视频生成任务状态的响应。

    状态枚举: submitted / queued / running / succeeded / failed / expired / cancelled
    """
    id: Optional[str] = Field(default=None, description="任务 ID")
    model: Optional[str] = Field(default=None, description="模型名称")
    status: str = Field(
        ...,
        description=(
            "任务状态: submitted / queued / running / "
            "succeeded / failed / expired / cancelled"
        ),
    )
    created_at: Optional[int] = Field(
        default=None, description="创建时间 (Unix 时间戳)",
    )
    updated_at: Optional[int] = Field(
        default=None, description="更新时间 (Unix 时间戳)",
    )
    content: Optional[Seedance2TaskContent] = None
    usage: Optional[Seedance2TaskUsage] = None
    error: Optional[Seedance2TaskError] = None

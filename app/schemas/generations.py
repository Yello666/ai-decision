from __future__ import annotations

from typing import Annotated, Optional, List, Literal, Union
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.hotspot import TrendObject, BrandObject
from app.schemas.product import ProductObject

# --------------------------
# 轮询/详情：统一生成任务
# --------------------------
class GenerationOut(BaseModel):
    id: int
    shopify_store_id: str
    type: str  # video | image | text
    status: str
    thread_id: Optional[str] = None
    segment_id: Optional[int] = None
    prompt_used: Optional[str] = None
    trend_snapshot: Optional[dict] = None
    brand_snapshot: Optional[dict] = None
    external_id: Optional[str] = None
    result_url: Optional[str] = None
    result_text: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

#
# # --------------------------
# # 热点 + 品牌 + 产品 → 病毒短视频生成请求
# # --------------------------
# class TrendProductVideoRequest(BaseModel):
#     """结合热点、品牌、产品信息生成病毒式短视频广告的请求体"""
#
#     trendObject: TrendObject = Field(..., description="热点事件信息")
#     brandObject: BrandObject = Field(..., description="品牌信息（名称、人设、风格）")
#     productObject: ProductObject = Field(..., description="产品信息（名称、核心卖点）")
#     user_prompt: Optional[str] = Field(default=None, description="用户额外补充指令")
#
#     generation_type: Literal["text_to_video", "image_to_video", "ref_to_video"] = Field(
#         default="text_to_video",
#         description="生成模式: text_to_video / image_to_video / ref_to_video",
#     )
#     duration: Optional[int] = Field(default=5, ge=1, le=12, description="视频时长（秒）")
#     ratio: Optional[str] = Field(default="16:9", description="视频比例，如 16:9 / 9:16 / 1:1")
#     generate_audio: Optional[bool] = Field(
#         default=None,
#         description="是否生成音频（仅图生视频有效）",
#     )
#     image_urls: Optional[List[str]] = Field(
#         default=None,
#         description="参考图 URL 列表，用于 image_to_video / ref_to_video 模式",
#     )
#     watermark: Optional[bool] = Field(default=False, description="是否添加水印")
#
#
# # --------------------------
# # 通用：结合热点 + 品牌的生成请求基类
# # --------------------------
# # class ContentGenerateRequest(BaseModel):
# #     """保留兼容：旧版统一生成请求"""
# #     title: str
# #     prompt: str
#
#
# class GenerateVideoRequest(BaseModel):
#     trend: TrendObject = Field(..., description="热点信息，用于生成与热点契合的视频描述")
#     brand: Optional[BrandObject] = Field(default=None, description="品牌信息；不传则使用当前商户已保存的品牌")
#     product: ProductObject= Field(default=None, description="单个商品信息；基于这个商品进行视频生成")
#     user_prompt: Optional[str] = Field(default=None, description="用户补充描述，会与模板组合成最终 prompt")
#     model: str = Field(default="doubao-seedance-1-5-pro", description="SeedDance 模型 ID")
#     generation_type: Literal["text_to_video", "image_to_video"] = "text_to_video"
#     image_url: Optional[str] = Field(default=None, description="参考图 URL，仅 image_to_video 时需要")
#     aspect_ratio: str = Field(default="16:9", description="画面比例")
#     duration: float = Field(default=5, ge=1, le=10, description="视频时长（秒）")
#     resolution: str = Field(default="720p", description="输出分辨率")
#
#
# class GenerateVideoResponse(BaseModel):
#     generation_id: int = Field(..., description="本系统生成任务 ID，用于轮询")
#     external_id: Optional[str] = Field(default=None, description="SeedDance video_id")
#     status: str = Field(..., description="pending | processing")
#     message: str = Field(default="任务已提交，请轮询 GET /content/generations/{generation_id} 获取结果")

#
# # --------------------------
# # 图片生成（SeedDance Nano Banana）
# # --------------------------
# class GenerateImageRequest(BaseModel):
#     trend: TrendObject = Field(..., description="热点信息")
#     brand: Optional[BrandObject] = Field(default=None, description="品牌信息；不传则使用当前商户已保存的品牌")
#     user_prompt: Optional[str] = Field(default=None, description="用户补充描述")
#     model: str = Field(default="seedream-4.5", description="图片模型 ID，须与 SeedDance 上游一致；可用 SEEDANCE_IMAGE_MODEL 配置默认值")
#     resolution: Literal["1k", "2k", "4k"] = "2k"
#     aspect_ratio: str = Field(default="1:1", description="画面比例")
#     output_format: Literal["png", "jpg", "webp"] = "png"
#     reference_image_urls: Optional[List[str]] = Field(default=None, description="参考图 URL 列表（编辑模式）")
#
#
# class GenerateImageResponse(BaseModel):
#     generation_id: int = Field(..., description="本系统生成任务 ID")
#     external_id: Optional[str] = Field(default=None, description="第三方任务 ID（若有）")
#     status: str = Field(..., description="pending | processing | completed")
#     result_url: Optional[str] = Field(default=None, description="完成后图片 URL")
#     message: str = Field(default="任务已提交，请轮询 GET /content/generations/{generation_id} 获取结果")
#

# # --------------------------
# # 文字生成（大模型）
# # --------------------------
# class GenerateTextRequest(BaseModel):
#     trend: TrendObject = Field(..., description="热点信息")
#     brand: Optional[BrandObject] = Field(default=None, description="品牌信息；不传则使用当前商户已保存的品牌")
#     user_prompt: Optional[str] = Field(default=None, description="用户补充要求，如字数、风格")
#     title: Optional[str] = Field(default=None, description="内容标题，用于存储与展示")
#
#
# class GenerateTextResponse(BaseModel):
#     generation_id: int = Field(..., description="本系统生成任务 ID（对应 generations 表）")
#     status: str = Field(..., description="completed（文字生成一般为同步）")
#     result_text: str = Field(..., description="生成的文案")
#     message: str = Field(default="success")



#
# # ----------------------------------------------------------
# # Seedance 1.5 Pro 视频生成 (火山引擎方舟官方 API 格式)
# # POST   /api/v3/contents/generations/tasks      创建任务
# # GET    /api/v3/contents/generations/tasks/{id}  查询任务
# # ----------------------------------------------------------
#
# class TextContentItem(BaseModel):
#     """content 数组中的文本项"""
#     type: Literal["text"] = "text"
#     text: str = Field(..., description="输入给模型的文本内容，描述期望生成的视频")
#
#
# class ImageUrlObject(BaseModel):
#     url: str = Field(..., description="图片 URL 地址")
#
#
# class ImageUrlContentItem(BaseModel):
#     """content 数组中的图片项"""
#     type: Literal["image_url"] = "image_url"
#     image_url: ImageUrlObject = Field(..., description="图片对象")
#     role: Optional[str] = Field(default=None, description="图片的位置或用途")
#
#
# ContentItem = Annotated[
#     Union[TextContentItem, ImageUrlContentItem],
#     Field(discriminator="type"),
# ]


# # --------------------------
# # 图片生成（SeedDance Nano Banana）
# # --------------------------
# class GenerateImageRequest(BaseModel):
#     trend: TrendObject = Field(..., description="热点信息")
#     brand: Optional[BrandObject] = Field(default=None, description="品牌信息；不传则使用当前商户已保存的品牌")
#     user_prompt: Optional[str] = Field(default=None, description="用户补充描述")
#     model: str = Field(default="seedream-4.5", description="图片模型 ID，须与 SeedDance 上游一致；可用 SEEDANCE_IMAGE_MODEL 配置默认值")
#     resolution: Literal["1k", "2k", "4k"] = "2k"
#     aspect_ratio: str = Field(default="1:1", description="画面比例")
#     output_format: Literal["png", "jpg", "webp"] = "png"
#     reference_image_urls: Optional[List[str]] = Field(default=None, description="参考图 URL 列表（编辑模式）")
#
#
# class GenerateImageResponse(BaseModel):
#     generation_id: int = Field(..., description="本系统生成任务 ID")
#     external_id: Optional[str] = Field(default=None, description="第三方任务 ID（若有）")
#     status: str = Field(..., description="pending | processing | completed")
#     result_url: Optional[str] = Field(default=None, description="完成后图片 URL")
#     message: str = Field(default="任务已提交，请轮询 GET /content/generations/{generation_id} 获取结果")


# # --------------------------
# # 文字生成（大模型）
# # --------------------------
# class GenerateTextRequest(BaseModel):
#     trend: TrendObject = Field(..., description="热点信息")
#     brand: Optional[BrandObject] = Field(default=None, description="品牌信息；不传则使用当前商户已保存的品牌")
#     user_prompt: Optional[str] = Field(default=None, description="用户补充要求，如字数、风格")
#     title: Optional[str] = Field(default=None, description="内容标题，用于存储与展示")
#
#
# class GenerateTextResponse(BaseModel):
#     generation_id: int = Field(..., description="本系统生成任务 ID（对应 generations 表）")
#     status: str = Field(..., description="completed（文字生成一般为同步）")
#     result_text: str = Field(..., description="生成的文案")
#     message: str = Field(default="success")



#
#
# # ----------------------------------------------------------
# # Seedance 1.5 Pro 视频生成 (火山引擎方舟官方 API 格式)
# # POST   /api/v3/contents/generations/tasks      创建任务
# # GET    /api/v3/contents/generations/tasks/{id}  查询任务
# # ----------------------------------------------------------
#
# class TextContentItem(BaseModel):
#     """content 数组中的文本项"""
#     type: Literal["text"] = "text"
#     text: str = Field(..., description="输入给模型的文本内容，描述期望生成的视频")
#
#
# class ImageUrlObject(BaseModel):
#     url: str = Field(..., description="图片 URL 地址")
#
#
# class ImageUrlContentItem(BaseModel):
#     """content 数组中的图片项"""
#     type: Literal["image_url"] = "image_url"
#     image_url: ImageUrlObject = Field(..., description="图片对象")
#     role: Optional[str] = Field(default=None, description="图片的位置或用途")
#
#
# ContentItem = Annotated[
#     Union[TextContentItem, ImageUrlContentItem],
#     Field(discriminator="type"),
# ]


# class _VideoRequestBase(BaseModel):
#     """Seedance 1.5 Pro 视频生成请求公共字段（与火山引擎方舟 API 对齐）"""
#     model: str = Field(
#         default="ep-20260330165459-vmz9x",
#         description="模型 Endpoint ID 或 Model ID",
#     )
#     resolution: Optional[str] = Field(
#         default=None,
#         description="视频分辨率: 480p / 720p / 1080p，Seedance 1.5 Pro 默认 720p",
#     )
#     ratio: Optional[str] = Field(default=None, description="视频宽高比: 16:9 / 4:3 / 1:1 / 3:4 / 9:16 / 21:9 / adaptive")
#     duration: Optional[int] = Field(default=5, description="视频时长(秒)，Seedance 1.5 Pro 支持 4~12 或 -1(自动)")
#     watermark: Optional[bool] = Field(default=False, description="是否添加水印")
#     generate_audio: Optional[bool] = Field(
#         default=None,
#         description="是否生成同步音频(仅 Seedance 1.5 Pro)，默认 true",
#     )
#     seed: Optional[int] = Field(default=None, description="随机种子，-1 为随机")
#     camera_fixed: Optional[bool] = Field(default=None, description="是否固定摄像头")
#     draft: Optional[bool] = Field(default=None, description="是否开启样片模式(仅 Seedance 1.5 Pro)")
#     return_last_frame: Optional[bool] = Field(default=None, description="是否返回尾帧图像")
#     callback_url: Optional[str] = Field(default=None, description="任务结果回调通知地址")
#     execution_expires_after: Optional[int] = Field(default=None, description="任务超时阈值(秒)，默认 172800")

#
# class Text2VideoRequest(_VideoRequestBase):
#     """文生视频请求结构体 —— content 仅包含 text 项"""
#     content: List[TextContentItem] = Field(..., description="文本内容数组")
#     ratio: Optional[str] = Field(default="16:9", description="视频宽高比，Seedance 1.5 Pro 文生视频默认 adaptive")
#
#
# class Image2VideoRequest(_VideoRequestBase):
#     """图生视频请求结构体 —— content 包含 text 项和 image_url 项"""
#     content: List[ContentItem] = Field(
#         ..., description="内容数组，包含文本和图片信息",
#     )
#     ratio: Optional[str] = Field(default="adaptive", description="视频宽高比，图生视频默认 adaptive")


# # ----------------------------------------------------------
# # Seedance 1.0 Lite i2v — 参考图生视频
# # 支持 1~4 张参考图(role=reference_image) + 可选文本提示词
# # ----------------------------------------------------------
#
# class Ref2VideoRequest(BaseModel):
#     """参考图生视频请求 —— Seedance 1.0 Lite i2v，支持 1~4 张参考图"""
#     model: str = Field(
#         default="ep-20260331152207-2n5zd",
#         description="Seedance 1.0 Lite i2v 的 Endpoint ID 或 Model ID",
#     )
#     content: List[ContentItem] = Field(
#         ...,
#         description="内容数组: 可选文本提示词 + 1~4 张参考图(role=reference_image)",
#     )
#     resolution: Optional[str] = Field(
#         default=None,
#         description="视频分辨率: 480p / 720p（参考图场景不支持 1080p）",
#     )
#     ratio: Optional[str] = Field(default="16:9", description="视频宽高比，参考图场景默认 16:9，不支持 adaptive")
#     duration: Optional[int] = Field(default=5, description="视频时长(秒)，2~12")
#     watermark: Optional[bool] = Field(default=False, description="是否添加水印")
#     seed: Optional[int] = Field(default=None, description="随机种子，-1 为随机")
#     return_last_frame: Optional[bool] = Field(default=None, description="是否返回尾帧图像")
#     callback_url: Optional[str] = Field(default=None, description="任务结果回调通知地址")
#     execution_expires_after: Optional[int] = Field(default=None, description="任务超时阈值(秒)，默认 172800")

#
# # ---------- 创建任务响应 ----------
#
# class CreateVideoTaskResponse(BaseModel):
#     """创建视频生成任务后的响应"""
#     id: str = Field(..., description="视频生成任务 ID，如 cgt-20250918170228-dw9rb")
#     status: str = Field(..., description="任务状态，提交成功时为 submitted")
#


#
# class VideoTaskStatusResponse(BaseModel):
#     """查询视频生成任务状态的响应"""
#     id: Optional[str] = Field(default=None, description="任务 ID")
#     model: Optional[str] = Field(default=None, description="模型名称-版本")
#     status: str = Field(
#         ...,
#         description="任务状态: queued / running / succeeded / failed / cancelled",
#     )
#     created_at: Optional[int] = Field(default=None, description="创建时间 (Unix 时间戳)")
#     updated_at: Optional[int] = Field(default=None, description="更新时间 (Unix 时间戳)")
#     content: Optional[VideoTaskContent] = None
#     usage: Optional[VideoTaskUsage] = None
#     error: Optional[VideoTaskError] = None
#


from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import (
    GenerateVideoRequest,
    GenerateVideoResponse,
    GenerateImageRequest,
    GenerateImageResponse,
    GenerateTextRequest,
    GenerateTextResponse,
    GenerationOut,
)
from app.services.content_service import (
    get_brand_for_store,
    create_video_generation,
    create_image_generation,
    create_text_generation,
    create_deprecated_text_record,
    get_generation_by_id,
    list_generations,
    refresh_video_status,
)
from app.services import seedance_client

router = APIRouter(prefix="/content", tags=["content"])


def _resolve_brand(request_brand, current_merchant: Merchant, db: Session):
    """若请求体带 brand 则用请求体，否则从 DB 取当前商户品牌。"""
    if request_brand is not None:
        return request_brand
    brand = get_brand_for_store(db, current_merchant.id)
    if not brand:
        raise HTTPException(
            status_code=400,
            detail="请先在 /merchant/brand-info 设置品牌信息，或在本请求中传入 brand",
        )
    return brand


# --------------------------
# 参考图上传（用于 image_to_video）
# --------------------------
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.post("/upload-reference-image", response_model=dict)
async def upload_reference_image(
    image: UploadFile = File(..., description="参考图文件，JPG/PNG/WebP，最大 10MB"),
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    上传参考图到 SeedDance，返回 hosted URL。
    用于 image_to_video 模式：先调用此接口获取 image_url，再在 generate-video 请求中传入。
    """
    if not image.content_type or image.content_type.lower() not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="仅支持图片格式：JPG、PNG、WebP",
        )
    try:
        seedance_client._headers()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        content = await image.read()
        filename = image.filename or "image.png"
        url = await seedance_client.upload_reference_image(content, filename)
        return success({"image_url": url})
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


# --------------------------
# 视频生成（fake SeedDance）文生视频/图生视频都有，在payload里面设置
# 如果要上传图片，需要先调用upload-reference-image接口获取image_url
# --------------------------
@router.post("/generate-video", response_model=dict)
async def generate_video(
    payload: GenerateVideoRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """结合热点与品牌生成视频。需配置 SEEDANCE_API_KEY。image_to_video 模式需先调用 upload-reference-image 获取 image_url。"""
    try:
        seedance_client._headers()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if payload.product.image_url!="":
        payload.image_url=payload.product.image_url
    if payload.generation_type == "image_to_video" and not payload.image_url:
        raise HTTPException(
            status_code=400,
            detail="image_to_video 模式必须提供 image_url，请先调用 POST /content/upload-reference-image 上传参考图",
        )
    brand = _resolve_brand(payload.brand, current_merchant, db)
    gen = await create_video_generation(
        db,
        current_merchant.shopify_store_id,
        payload.trend,
        brand,
        product=payload.product,
        user_prompt=payload.user_prompt,
        model=payload.model,
        generation_type=payload.generation_type,
        image_url=payload.image_url,
        aspect_ratio=payload.aspect_ratio,
        duration=payload.duration,
        resolution=payload.resolution,
    )
    return success(
        GenerateVideoResponse(
            generation_id=gen.id,
            external_id=gen.external_id,
            status=gen.status,
            message="任务已提交，请轮询 GET /api/v1/content/generations/{generation_id} 获取结果",
        )
    )

#
# @router.post("/generate-video-with-image", response_model=dict)
# def generate_video_with_image(
#     data: str = Form(..., description="JSON 字符串，包含 trend、brand、user_prompt 等，同 generate-video 请求体"),
#     reference_image: UploadFile = File(..., description="参考图文件，将自动上传并用于 image_to_video"),
#     current_merchant: Merchant = Depends(get_current_merchant),
#     db: Session = Depends(get_db),
# ):
#     """
#     一步完成：上传参考图并发起 image_to_video 生成。
#     使用 multipart/form-data：data（JSON）+ reference_image（文件）。
#     """
#     try:
#         seedance_client._headers()
#     except ValueError as e:
#         raise HTTPException(status_code=503, detail=str(e))
#     if not reference_image.content_type or reference_image.content_type.lower() not in ALLOWED_IMAGE_TYPES:
#         raise HTTPException(status_code=400, detail="仅支持图片格式：JPG、PNG、WebP")
#     try:
#         payload = GenerateVideoRequest.model_validate_json(data)
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=f"data 必须是有效的 JSON：{e}")
#     try:
#         image_url = seedance_client.upload_reference_image(reference_image.file)
#     except RuntimeError as e:
#         raise HTTPException(status_code=502, detail=str(e))
#     payload = payload.model_copy(
#         update={"image_url": image_url, "generation_type": "image_to_video"}
#     )
#     brand = _resolve_brand(payload.brand, current_merchant, db)
#     gen = create_video_generation(
#         db,
#         current_merchant.shopify_store_id,
#         payload.trend,
#         brand,
#         user_prompt=payload.user_prompt,
#         model=payload.model,
#         generation_type=payload.generation_type,
#         image_url=payload.image_url,
#         aspect_ratio=payload.aspect_ratio,
#         duration=payload.duration,
#         resolution=payload.resolution,
#     )
#     return success(
#         GenerateVideoResponse(
#             generation_id=gen.id,
#             external_id=gen.external_id,
#             status=gen.status,
#             message="任务已提交，请轮询 GET /api/v1/content/generations/{generation_id} 获取结果",
#         )
#     )


# --------------------------
# 图片生成（还没确认使用哪一个模型，现在是请求不通的）
# --------------------------
@router.post("/generate-image", response_model=dict)
async def generate_image(
    payload: GenerateImageRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """结合热点与品牌生成图片。需配置 SEEDANCE_API_KEY。"""
    try:
        seedance_client._headers()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    brand = _resolve_brand(payload.brand, current_merchant, db)
    gen = await create_image_generation(
        db,
        current_merchant.shopify_store_id,
        payload.trend,
        brand,
        user_prompt=payload.user_prompt,
        model=payload.model,
        resolution=payload.resolution,
        aspect_ratio=payload.aspect_ratio,
        output_format=payload.output_format,
        reference_image_urls=payload.reference_image_urls,
    )
    return success(
        GenerateImageResponse(
            generation_id=gen.id,
            external_id=gen.external_id,
            status=gen.status,
            result_url=gen.result_url,
            message="任务已提交，请轮询 GET /api/v1/content/generations/{generation_id} 获取结果",
        )
    )


# --------------------------
# 文字生成（qwen3.5-plus）
# --------------------------
@router.post("/generate-text", response_model=dict)
def generate_text(
    payload: GenerateTextRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """结合热点与品牌生成营销文案。需配置 LLM_API_KEY / LLM_API_URL。"""
    brand = _resolve_brand(payload.brand, current_merchant, db)
    gen = create_text_generation(
        db,
        current_merchant.shopify_store_id,
        payload.trend,
        brand,
        user_prompt=payload.user_prompt,
        title=payload.title,
    )
    if gen.status == "failed":
        raise HTTPException(status_code=502, detail=gen.error_message or "文字生成失败")
    return success(
        GenerateTextResponse(
            generation_id=gen.id,
            status=gen.status,
            result_text=gen.result_text or "",
            message="success",
        )
    )


# --------------------------
# 轮询生成任务状态（视频/图片/文字统一）
# --------------------------
@router.get("/generations/{generation_id}", response_model=dict)
async def get_generation(
    generation_id: int,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    查询生成任务状态与结果。视频任务在 pending/processing 时会自动向 SeedDance 拉取最新状态。
    """
    gen = get_generation_by_id(db, generation_id, current_merchant.shopify_store_id)
    if not gen:
        raise HTTPException(status_code=404, detail="generation_not_found")
    if gen.type == "video" and gen.status in ("pending", "processing"):
        await refresh_video_status(db, gen)
    return success(GenerationOut.model_validate(gen))


# --------------------------
# 生成任务列表
# --------------------------
@router.get("/generations", response_model=dict)
def generation_list(
    skip: int = 0,
    limit: int = 20,
    type: str | None = None,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """分页查询当前商户的视频/图片/文字生成记录。type 可选：video | image | text。"""
    items = list_generations(db, current_merchant.shopify_store_id, type_filter=type, skip=skip, limit=limit)
    return success([GenerationOut.model_validate(g) for g in items])


# # --------------------------
# # 兼容旧接口：统一 generate（保留占位，建议迁移到上述三个接口）
# # --------------------------
# @router.post("/generate")
# def generate_content(
#     payload: ContentGenerateRequest,
#     current_merchant: Merchant = Depends(get_current_merchant),
#     db: Session = Depends(get_db),
# ):
#     """
#     旧版统一生成接口（占位）。建议使用：
#     - POST /content/generate-video
#     - POST /content/generate-image
#     - POST /content/generate-text
#     """
#     generated_text = (
#         f"[Deprecated] 请使用 /content/generate-text 并传入 trend + brand。"
#         f"原请求: title={payload.title}, prompt={payload.prompt}"
#     )
#     gen = create_deprecated_text_record(
#         db,
#         current_merchant.shopify_store_id,
#         payload.title,
#         payload.prompt,
#         generated_text,
#     )
#     return success(GenerationOut.model_validate(gen))


# # --------------------------
# # 文字内容列表（原有）
# # --------------------------
# @router.get("/list")
# def content_list(
#     skip: int = 0,
#     limit: int = 20,
#     current_merchant: Merchant = Depends(get_current_merchant),
#     db: Session = Depends(get_db),
# ):
#     """查看已生成的文字内容（来自 generations 表 type=text）。"""
#     items = list_generations(db, current_merchant.shopify_store_id, type_filter="text", skip=skip, limit=limit)
#     return success([GenerationOut.model_validate(g) for g in items])

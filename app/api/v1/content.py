from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import (
    ContentGenerateRequest,
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
# 视频生成（SeedDance）
# --------------------------
@router.post("/generate-video", response_model=dict)
def generate_video(
    payload: GenerateVideoRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """结合热点与品牌生成视频。需配置 SEEDANCE_API_KEY。"""
    try:
        seedance_client._headers()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    brand = _resolve_brand(payload.brand, current_merchant, db)
    gen = create_video_generation(
        db,
        current_merchant.shopify_store_id,
        payload.trend,
        brand,
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


# --------------------------
# 图片生成（还没确认使用哪一个模型，现在是请求不通的）
# --------------------------
@router.post("/generate-image", response_model=dict)
def generate_image(
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
    gen = create_image_generation(
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
def get_generation(
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
        refresh_video_status(db, gen)
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


# --------------------------
# 兼容旧接口：统一 generate（保留占位，建议迁移到上述三个接口）
# --------------------------
@router.post("/generate")
def generate_content(
    payload: ContentGenerateRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    旧版统一生成接口（占位）。建议使用：
    - POST /content/generate-video
    - POST /content/generate-image
    - POST /content/generate-text
    """
    generated_text = (
        f"[Deprecated] 请使用 /content/generate-text 并传入 trend + brand。"
        f"原请求: title={payload.title}, prompt={payload.prompt}"
    )
    gen = create_deprecated_text_record(
        db,
        current_merchant.shopify_store_id,
        payload.title,
        payload.prompt,
        generated_text,
    )
    return success(GenerationOut.model_validate(gen))


# --------------------------
# 文字内容列表（原有）
# --------------------------
@router.get("/list")
def content_list(
    skip: int = 0,
    limit: int = 20,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """查看已生成的文字内容（来自 generations 表 type=text）。"""
    items = list_generations(db, current_merchant.shopify_store_id, type_filter="text", skip=skip, limit=limit)
    return success([GenerationOut.model_validate(g) for g in items])

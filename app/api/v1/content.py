from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import (
    GenerateTextRequest,
    GenerateTextResponse,

)
from app.services.content_service import (
    get_brand_for_store,
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


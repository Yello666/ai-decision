import logging
from typing import Union

from fastapi import APIRouter, Depends, HTTPException
from httpx import HTTPStatusError
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import (
    GenerationOut,
    Text2VideoRequest,
    Image2VideoRequest,
    Ref2VideoRequest,
    CreateVideoTaskResponse,
    VideoTaskStatusResponse,
    TrendProductVideoRequest,
)
from app.services.content_service import (
    get_generation_by_id,
    refresh_video_status,
)
from app.services.content_service.trend_video_service import build_seedance_payload
from app.services.seedance_client import (
    create_seedance_video_task,
    query_seedance_video_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generate", tags=["generate"])


# ----------------------------------------------------------
# 视频生成 — Seedance 1.5 Pro (火山引擎方舟)
# 文生视频 (text2video): content 只含 text 项
# 图生视频 (image2video): content 含 text + image_url 项
# ----------------------------------------------------------
@router.post("/generate-video", response_model=dict)
async def generate_video(
    payload: Union[Image2VideoRequest, Text2VideoRequest],
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    调用 Seedance 1.5 Pro 创建视频生成任务。

    - **文生视频**: content 仅包含 type=text 的项
    - **图生视频**: content 同时包含 type=text 和 type=image_url 的项

    请求/响应格式与火山引擎官方 API 一致。成功后返回任务 ID，
    可调用 GET /generate/video-task-status/{task_id} 轮询任务状态。
    """
    try:
        request_body = payload.model_dump(exclude_none=True)
        result = await create_seedance_video_task(request_body)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPStatusError as e:
        logger.error("Seedance create task upstream error: %s", e.response.text)
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"上游 API 错误: {e.response.text}",
        )
    except Exception as e:
        logger.exception("Seedance create task unexpected error")
        raise HTTPException(status_code=500, detail=str(e))

    return success(
        CreateVideoTaskResponse(
            id=result.get("id", ""),
            status=result.get("status", "submitted"),
        )
    )


# ----------------------------------------------------------
# 参考图生视频 — Seedance 1.0 Lite i2v
# content 含 1~4 张参考图(role=reference_image) + 可选文本
# ----------------------------------------------------------
@router.post("/ref2video", response_model=dict)
async def generate_ref2video(
    payload: Ref2VideoRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    调用 Seedance 1.0 Lite i2v 创建参考图生视频任务。

    - content 包含 **1~4 张参考图**（type=image_url, role=reference_image）
    - 可选文本提示词，推荐使用 "[图1]xxx，[图2]xxx" 格式指定图片组合
    - 参考图场景不支持 1080p 分辨率、不支持 adaptive 宽高比

    成功后返回任务 ID，可调用 GET /generate/video-task-status/{task_id} 轮询状态。
    """
    try:
        request_body = payload.model_dump(exclude_none=True)
        result = await create_seedance_video_task(request_body)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPStatusError as e:
        logger.error("Seedance Lite i2v create task upstream error: %s", e.response.text)
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"上游 API 错误: {e.response.text}",
        )
    except Exception as e:
        logger.exception("Seedance Lite i2v create task unexpected error")
        raise HTTPException(status_code=500, detail=str(e))

    return success(
        CreateVideoTaskResponse(
            id=result.get("id", ""),
            status=result.get("status", "submitted"),
        )
    )


# ----------------------------------------------------------
# 查询视频生成任务状态 — 通用（所有 Seedance 模型共用）
# ----------------------------------------------------------
@router.get("/video-task-status/{task_id}", response_model=dict)
async def get_video_task_status(
    task_id: str,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    查询 Seedance 1.5 Pro 视频生成任务的状态与结果。

    任务状态: queued → running → succeeded / failed / cancelled。
    succeeded 时 content.video_url 包含生成的视频地址（24h 有效，请及时转存）。
    """
    try:
        result = await query_seedance_video_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPStatusError as e:
        logger.error("Seedance query task upstream error: %s", e.response.text)
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"上游 API 错误: {e.response.text}",
        )
    except Exception as e:
        logger.exception("Seedance query task unexpected error")
        raise HTTPException(status_code=500, detail=str(e))

    return success(
        VideoTaskStatusResponse(
            id=result.get("id"),
            model=result.get("model"),
            status=result.get("status", "unknown"),
            created_at=result.get("created_at"),
            updated_at=result.get("updated_at"),
            content=result.get("content"),
            usage=result.get("usage"),
            error=result.get("error"),
        )
    )


# ----------------------------------------------------------
# 热点 × 品牌 × 产品 → 病毒式短视频广告
# 自动组装 Prompt，支持 text/image/ref 三种生成模式
# ----------------------------------------------------------
@router.post("/trend-product-video", response_model=dict)
async def generate_trend_product_video(
    payload: TrendProductVideoRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    结合热点、品牌调性和产品信息，生成夸张搞笑、具有病毒传播潜力的短视频广告。

    - **text_to_video**: 纯文生视频
    - **image_to_video**: 图生视频（1 张图 = 首帧，2 张图 = 首帧 + 尾帧）
    - **ref_to_video**: 参考图生视频（每张图作为 reference_image）

    成功后返回任务 ID，可调用 GET /generate/video-task-status/{task_id} 轮询状态。
    """
    try:
        request_body, prompt_used = await build_seedance_payload(payload)
        result = await create_seedance_video_task(request_body)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPStatusError as e:
        logger.error("Trend-product-video upstream error: %s", e.response.text)
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"上游 API 错误: {e.response.text}",
        )
    except Exception as e:
        logger.exception("Trend-product-video unexpected error")
        raise HTTPException(status_code=500, detail=str(e))

    return success(data={
        "id": result.get("id", ""),
        "prompt": prompt_used,
    })


# ----------------------------------------------------------
# 轮询内部生成任务状态（视频/图片/文字统一，基于本地 DB）
# ----------------------------------------------------------
@router.get("/generations/{generation_id}", response_model=dict)
async def get_generation(
    generation_id: int,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    查询本地生成任务状态与结果。视频任务在 pending/processing 时会自动向上游拉取最新状态。
    """
    gen = get_generation_by_id(db, generation_id, current_merchant.shopify_store_id)
    if not gen:
        raise HTTPException(status_code=404, detail="generation_not_found")
    if gen.type == "video" and gen.status in ("pending", "processing"):
        await refresh_video_status(db, gen)
    return success(GenerationOut.model_validate(gen))


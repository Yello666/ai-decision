import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from sqlalchemy.orm import Session

from app.api.deps import extract_access_token_from_cookies, get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import GenerationOut

from app.services.generation_service import (
    get_generation_by_id,
    list_generations_by_thread_id,
)
from app.services.generate_service import handle_generation_status_ws

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generations", tags=["generations"])

# ----------------------------------------------------------
# 查询内部生成任务状态（基于本地 DB）
# ----------------------------------------------------------
@router.get("/{generation_id}", response_model=dict)
async def get_generation(
    generation_id: int,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    查询本地生成任务状态与结果。
    """
    gen = get_generation_by_id(db, generation_id, current_merchant.shopify_store_id)
    if not gen:
        raise HTTPException(status_code=404, detail="generation_not_found")
    return success(GenerationOut.model_validate(gen))


# ----------------------------------------------------------
# 按视频生成会话 thread_id 查询其所有 Generation 记录
# 用途：一次 /video-thread/create 会拆分出多段视频任务，前端凭 thread_id
#      一次性获取本会话下全部视频任务的状态与结果。
# ----------------------------------------------------------
@router.get("/by-thread/{thread_id}", response_model=dict)
def get_generations_by_thread(
    thread_id: str,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    根据 thread_id 查询该视频生成会话下的全部 Generation 记录（按店铺隔离）。
    返回列表按 id 升序（与视频分段顺序一致）。
    """
    gens = list_generations_by_thread_id(
        db, thread_id, current_merchant.shopify_store_id
    )
    return success([GenerationOut.model_validate(g) for g in gens])

# # ----------------------------------------------------------
# # 查询视频生成任务状态 — 通用（所有 Seedance 模型共用）
# # ----------------------------------------------------------
# @router.get("/video-task-status/{task_id}", response_model=dict)
# async def get_video_task_status(
#     task_id: str,
#     current_merchant: Merchant = Depends(get_current_merchant),
# ):
#     """
#     查询 Seedance 1.5 Pro 视频生成任务的状态与结果。

#     任务状态: queued → running → succeeded / failed / cancelled。
#     succeeded 时 content.video_url 包含生成的视频地址（24h 有效，请及时转存）。
#     """
#     result = await query_video_task_status(task_id)
#     return success(result)
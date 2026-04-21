import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.content import (
    GenerationOut,
    VideoTaskStatusResponse,
    VideoTaskCallbackRequest,
)
from app.services.content_service import (
    get_generation_by_id,
    list_generations_by_thread_id,
)
from app.services.generate_service import (
    handle_generation_status_ws,
    process_video_task_callback,
    query_video_task_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generate", tags=["generate"])


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


# ----------------------------------------------------------
# 方舟平台回调 — 接收视频生成任务状态推送（无需鉴权）
# 幂等设计：终态记录不重复处理；5s 内必须响应 200
# ----------------------------------------------------------
@router.post("/callback", response_model=dict)
async def video_task_callback(
    payload: VideoTaskCallbackRequest,
    db: Session = Depends(get_db),
):
    """
    接收方舟平台 POST 推送的视频任务状态回调。

    状态枚举: queued / running / succeeded / failed / expired。
    当 status=succeeded 时，将完整 generation 数据持久化到数据库。
    若该任务属于串行链条（Redis seq_chain:{task_id}），自动提交下一段。
    方舟平台在 5s 内未收到成功响应时会重试最多 3 次，本接口保证幂等。
    """
    result = await process_video_task_callback(payload, db)
    return success(data=result)


# ----------------------------------------------------------
# WebSocket — 实时推送视频生成状态变更到前端
# ws(s)://host/api/v1/generate/ws/status?token=<JWT>
#
# 鉴权：通过 query param 传递 JWT access_token
# 通道：每个商户订阅独立 Redis channel gen:status:{store_id}
#
# 心跳协议（仅接受 JSON 文本帧）：
#   服务端 → 客户端: {"event":"ping"}   连接建立后立即发一次，之后每 WS_HEARTBEAT_INTERVAL_SECONDS 秒
#   客户端 → 服务端: {"event":"pong"}   收到 ping 后尽快回复
#   客户端主动下线 : {"event":"close"}  收到后服务端清理并断开
#   任意带合法 event 的 JSON 对象（除 close 外）均刷新「客户端活跃时间」
# 若超过 WS_PONG_TIMEOUT_SECONDS 未刷新活跃时间，服务端判定离线并断开。
# ----------------------------------------------------------
@router.websocket("/ws/status")
async def ws_generation_status(
    websocket: WebSocket,
    token: str = Query(...),
):
    await handle_generation_status_ws(websocket, token)


# ----------------------------------------------------------
# 查询内部生成任务状态（基于本地 DB）
# ----------------------------------------------------------
@router.get("/generations/{generation_id}", response_model=dict)
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
@router.get("/generations/by-thread/{thread_id}", response_model=dict)
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
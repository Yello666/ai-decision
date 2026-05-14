"""
视频生成会话 API —— 基于 LangGraph 状态机。

核心接口：
  GET    /video-thread/list                 分页列出当前商户的历史会话（轻量索引）
  POST   /video-thread/create               异步非阻塞，立即返回 thread_id
  POST   /video-thread/{thread_id}/resume   注入 human 决策，恢复 Graph
  GET    /video-thread/{thread_id}/state    单次拉取前端视图态（降级兜底）
  GET    /video-thread/{thread_id}/history  回放对话过程（历次草稿 + 用户决策）
  GET    /video-thread/{thread_id}/stream   SSE 实时事件流（可带 ?access_token=；亦兼容 Cookie / Bearer）
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant, get_current_merchant_sse
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.video_thread import (
    CreateThreadRequest,
    ResumeThreadRequest,
    UpdateThreadParamsRequest,
    VideoThreadStatus,
    VideoTaskCallbackRequest,
)
from app.services.video_thread_service import (
    create_thread_task,
    get_thread_conversation_history,
    get_thread_view_state,
    list_video_threads,
    process_video_task_callback,
    resume_thread_task,
    stream_thread_events_response,
    update_thread_params,
)

router = APIRouter(prefix="/video-thread", tags=["video-thread"])


# ──────────────────────────────────────────────
# GET /video-thread/list
#   分页列出当前商户的历史会话（轻量索引，不含 segments 详情）
# ──────────────────────────────────────────────
@router.get("/list", response_model=dict)
async def list_threads(
    status: Optional[VideoThreadStatus] = Query(
        default=None,
        description="按状态过滤：running / waiting_human / finished / error；不传即全部",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="每页数量，最大 100"),
    offset: int = Query(default=0, ge=0, description="分页偏移"),
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    列表页数据源。点击某条记录后，前端再调 `GET /video-thread/{thread_id}/state`
    拉取完整对话（含 segments、revision_count、task_results 等）。
    """
    data = await list_video_threads(
        current_merchant, status=status, limit=limit, offset=offset,
    )
    return success(data=data.model_dump(mode="json"))


# ──────────────────────────────────────────────
# POST /video-thread/create
#   立即返回 thread_id；真正的 Graph 执行放到后台任务
# ──────────────────────────────────────────────
@router.post("/create", response_model=dict)
async def create_thread(
    payload: CreateThreadRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    创建视频生成会话。本接口立刻返回 thread_id（<=50ms），
    客户端应随即：
      1. 建立 SSE: GET /video-thread/{thread_id}/stream（可 ?access_token=<JWT>）
      2. 保底轮询: GET /video-thread/{thread_id}/state
    """
    data = await create_thread_task(payload, current_merchant)
    return success(data=data)


# ──────────────────────────────────────────────
# POST /video-thread/{thread_id}/resume
# 用户决策接口，用于注入用户决策（approve / edit / feedback），恢复 Graph 执行。
# ──────────────────────────────────────────────
@router.post("/{thread_id}/resume", response_model=dict)
async def resume_thread(
    thread_id: str,
    payload: ResumeThreadRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    注入人类决策（approve / edit / feedback），恢复 Graph 执行。
    接口本身立刻返回，真正的后续节点（可能耗时数十秒）在后台运行。
    """
    data = await resume_thread_task(thread_id, payload, current_merchant)
    return success(data=data)

# ──────────────────────────────────────────────
# GET /video-thread/{thread_id}/state
#   返回 FrontendViewState，用于刷新恢复 / SSE 降级兜底
# ──────────────────────────────────────────────
@router.get("/{thread_id}/state", response_model=dict)
async def get_thread_state(
    thread_id: str,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    单次拉取当前视图态。两个使用场景：
      1. 页面首次打开 / 刷新 → 先调用本接口，快速把 UI 定位到正确位置
      2. SSE 发生无法恢复的断开 → 前端降级为对本接口的定时轮询
    """
    data = await get_thread_view_state(thread_id, current_merchant)
    return success(data=data)


# ──────────────────────────────────────────────
# GET /video-thread/{thread_id}/history
#   回放对话过程：历次 LLM 草稿 + 每一次用户决策（approve / edit / feedback）
# ──────────────────────────────────────────────
@router.get("/{thread_id}/history", response_model=dict)
async def get_thread_history(
    thread_id: str,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    返回一个 video thread 从创建到当前的完整对话时间线，供列表页点击后
    在详情抽屉 / 时间线组件中渲染。

    每条 turn 的 ``kind`` 之一：
      - ``user_input``       用户最初输入的想法
      - ``assistant_draft``  LLM 生成 / 编辑 / 重写产出的一版分镜草稿
      - ``user_action``      用户的决策（approve / edit / feedback + 反馈 / 编辑明细）
      - ``submitted``        已向 Seedance 提交，不再有新草稿

    响应根级还包含 ``product``、``product_for_prompt``（来自当前 graph state 快照；
    checkpoint 缺失时可能为 null）。

    数据来源为 LangGraph Postgres checkpoint。超过 TTL 的老 thread 可能
    丢失中间草稿，但会兜底返回一条 user_input。
    """
    data = await get_thread_conversation_history(thread_id, current_merchant)
    return success(data=data.model_dump(mode="json"))


# ──────────────────────────────────────────────
# GET /video-thread/{thread_id}/stream    （SSE）
# 鉴权：Query access_token 优先；否则同 REST（Cookie / Authorization: Bearer）
# ──────────────────────────────────────────────
@router.get("/{thread_id}/stream")
async def stream_thread_events(
    thread_id: str,
    request: Request,
    current_merchant: Merchant = Depends(get_current_merchant_sse),
):
    return await stream_thread_events_response(thread_id, request, current_merchant)

# ----------------------------------------------------------
# 方舟平台回调 — 接收视频生成任务状态推送（无需鉴权）
# 幂等设计：终态记录不重复处理；5s 内必须响应 200
# POST /video-thread/callback
# ----------------------------------------------------------
@router.post("/callback", response_model=dict)
async def video_task_callback(
    payload: VideoTaskCallbackRequest,
    db: Session = Depends(get_db),
):
    """
    接收方舟平台 POST 推送的视频任务状态回调。

    状态枚举: queued / running / succeeded / failed / expired / cancelled。
    当 status=succeeded 时，将完整 generation 数据持久化到数据库。
    若该任务属于串行链条（Redis seq_chain:{task_id}），自动提交下一段。
    方舟平台在 5s 内未收到成功响应时会重试最多 3 次，本接口保证幂等。
    """
    result = await process_video_task_callback(payload, db)
    return success(data=result)




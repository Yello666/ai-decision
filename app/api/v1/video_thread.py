"""
视频生成会话 API —— 基于 LangGraph 状态机。

核心接口：
  POST   /video-thread/create               异步非阻塞，立即返回 thread_id
  POST   /video-thread/{thread_id}/resume   注入 human 决策，恢复 Graph
  GET    /video-thread/{thread_id}/state    单次拉取前端视图态（降级兜底）
  GET    /video-thread/{thread_id}/stream   SSE 实时事件流（细粒度进度 / human_action / done / error）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.schemas.video_thread import CreateThreadRequest, ResumeThreadRequest
from app.services.video_thread_service import (
    create_thread_task,
    get_thread_view_state,
    resume_thread_task,
    stream_thread_events_response,
)

router = APIRouter(prefix="/video-thread", tags=["video-thread"])


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
      1. 建立 SSE: GET /video-thread/{thread_id}/stream
      2. 保底轮询: GET /video-thread/{thread_id}/state
    """
    data = await create_thread_task(payload, current_merchant)
    return success(data=data)


# ──────────────────────────────────────────────
# POST /video-thread/{thread_id}/resume
#   同样异步非阻塞，立即返回
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
# GET /video-thread/{thread_id}/stream    （SSE）
# ──────────────────────────────────────────────
@router.get("/{thread_id}/stream")
async def stream_thread_events(
    thread_id: str,
    request: Request,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    SSE 事件流。前端示例：

        const es = new EventSource(`/video-thread/${threadId}/stream`, { withCredentials: true });
        es.addEventListener("progress", e => {...});
        es.addEventListener("human_action_required", e => {...});
        es.addEventListener("done", e => { es.close(); });
        es.addEventListener("error", e => { es.close(); /* 降级轮询 /state */ });

    实现要点：
      - 先发送一次 `state` 事件，把最新视图态推给刚连上的客户端，避免空窗期。
      - 订阅 EventBus；遇到 `done` / `error` 主动关闭连接。
      - 每 15s 发一个 `: ping` 注释帧保活，穿透 nginx / 浏览器空闲断开。
      - 断联（`request.is_disconnected`）时及时 unsubscribe 释放资源。
    """
    return await stream_thread_events_response(thread_id, request, current_merchant)

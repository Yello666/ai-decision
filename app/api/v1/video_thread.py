"""
视频生成会话 API —— 基于 LangGraph 状态机。

提供三个核心接口:
  POST   /video-thread/create    创建会话，启动 Graph
  POST   /video-thread/{id}/resume  注入人类输入，恢复 Graph
  GET    /video-thread/{id}/state   查询当前会话状态
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.schemas.video_thread import CreateThreadRequest, ResumeThreadRequest
from app.services.video_graph.graph import get_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video-thread", tags=["video-thread"])


# ──────────────────────────────────────────────
# POST /video-thread/create
# ──────────────────────────────────────────────
@router.post("/create", response_model=dict)
async def create_thread(
    payload: CreateThreadRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    创建视频生成会话，启动 LangGraph 工作流。
    Graph 会执行到 human_interrupt 节点后暂停，等待 resume。

    **media_assets**（可选）建议结构：
    ```json
    {
      "ref_image_urls": ["https://example.com/a.jpg"],
      "first_frame_url": "https://example.com/first.jpg",
      "last_frame_url": "https://example.com/last.jpg"
    }
    ```
    纯文生视频可省略 `media_assets`，或仅传空对象/空列表字段。
    """
    thread_id = str(uuid.uuid4())

    initial_state = {
        "user_input": payload.user_input,
        "trend": payload.trend.model_dump(mode="json"),
        "brand": payload.brand.model_dump(mode="json"),
        "product": payload.product.model_dump(mode="json"),
        "generation_mode": payload.generation_mode,
        "media_assets": payload.media_assets.model_dump(mode="json") if payload.media_assets else {},
        "config_params": payload.config_params.model_dump(mode="json") if payload.config_params else {},
        "shopify_store_id": current_merchant.shopify_store_id,
        "revision_count": 0,
    }

    config = {"configurable": {"thread_id": thread_id}}
    graph = get_graph()

    try:
        result = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        if "interrupt" not in str(type(e).__name__).lower():
            logger.exception("Graph 执行异常: thread_id=%s", thread_id)
            raise HTTPException(status_code=500, detail=f"工作流启动失败: {e}")

    snapshot = await graph.aget_state(config)
    current_state = snapshot.values if snapshot else {}
    if current_state.get("error") or current_state.get("current_step") == "error":
        return success(data={
            "thread_id": thread_id,
            "status": "error",
            "error": current_state.get("error", "工作流初始化失败"),
        })

    segments = current_state.get("script_segments", [])
    display_config = current_state.get("parsed_config", {})
    lang = display_config.get("language", "zh")
    display_segments = _format_segments_for_display(segments, lang)

    return success(data={
        "thread_id": thread_id,
        "status": "waiting_human",
        "segments": display_segments,
        "total_duration": current_state.get("total_duration", 0),
        "execution_strategy": current_state.get("execution_strategy", "parallel"),
        "optimized_prompt": current_state.get("optimized_prompt", ""),
    })


# ──────────────────────────────────────────────
# POST /video-thread/{thread_id}/resume
# ──────────────────────────────────────────────
@router.post("/{thread_id}/resume", response_model=dict)
async def resume_thread(
    thread_id: str,
    payload: ResumeThreadRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """
    注入人类决策（approve / edit / feedback），恢复 Graph 执行。
    - approve: Graph 继续到 assemble_and_submit → respond → END
    - edit:    Graph 到 apply_edit → 再次中断等确认
    - feedback: Graph 到 revise_script（LLM 重写）→ 再次中断等确认
    """
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_graph()

    snapshot = await graph.aget_state(config)
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    store_id = snapshot.values.get("shopify_store_id", "")
    if store_id != current_merchant.shopify_store_id:
        raise HTTPException(status_code=403, detail="无权操作此会话")

    human_input = {
        "action": payload.action,
        "edited_segments": [s.model_dump(exclude_none=True) for s in payload.edited_segments],
        "feedback": payload.feedback,
    }

    try:
        result = await graph.ainvoke(
            Command(resume=human_input),
            config=config,
        )
    except Exception as e:
        if "interrupt" not in str(type(e).__name__).lower():
            logger.exception("Graph resume 异常: thread_id=%s", thread_id)
            raise HTTPException(status_code=500, detail=f"工作流恢复失败: {e}")

    snapshot = await graph.aget_state(config)
    current_state = snapshot.values if snapshot else {}
    step = current_state.get("current_step", "")

    if step == "done":
        return success(data={
            "thread_id": thread_id,
            "status": "completed",
            "task_results": current_state.get("task_results", []),
            "final_status": current_state.get("final_status", ""),
        })

    if current_state.get("error"):
        return success(data={
            "thread_id": thread_id,
            "status": "error",
            "error": current_state["error"],
        })

    segments = current_state.get("script_segments", [])
    display_config = current_state.get("parsed_config", {})
    lang = display_config.get("language", "zh")
    display_segments = _format_segments_for_display(segments, lang)

    return success(data={
        "thread_id": thread_id,
        "status": "waiting_human",
        "segments": display_segments,
        "total_duration": current_state.get("total_duration", 0),
        "execution_strategy": current_state.get("execution_strategy", "parallel"),
        "revision_count": current_state.get("revision_count", 0),
    })


# ──────────────────────────────────────────────
# GET /video-thread/{thread_id}/state
# ──────────────────────────────────────────────
@router.get("/{thread_id}/state", response_model=dict)
async def get_thread_state(
    thread_id: str,
    current_merchant: Merchant = Depends(get_current_merchant),
):
    """查询当前会话的完整状态（用于断线重连恢复）。"""
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_graph()

    snapshot = await graph.aget_state(config)
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    current_state = snapshot.values
    store_id = current_state.get("shopify_store_id", "")
    if store_id != current_merchant.shopify_store_id:
        raise HTTPException(status_code=403, detail="无权查看此会话")

    segments = current_state.get("script_segments", [])
    display_config = current_state.get("parsed_config", {})
    lang = display_config.get("language", "zh")
    display_segments = _format_segments_for_display(segments, lang)

    return success(data={
        "thread_id": thread_id,
        "current_step": current_state.get("current_step", "unknown"),
        "segments": display_segments,
        "total_duration": current_state.get("total_duration", 0),
        "execution_strategy": current_state.get("execution_strategy", "parallel"),
        "task_results": current_state.get("task_results", []),
        "final_status": current_state.get("final_status", ""),
        "revision_count": current_state.get("revision_count", 0),
        "error": current_state.get("error", ""),
    })


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _format_segments_for_display(segments: list[dict], language: str) -> list[dict]:
    """将 segments 格式化为前端友好的展示格式。"""
    result = []
    for seg in segments:
        result.append({
            "segment_id": seg.get("segment_id"),
            "description": seg.get("description_zh", seg.get("description", "")) if language == "zh" else seg.get("description", ""),
            "description_en": seg.get("description", ""),
            "duration": seg.get("duration"),
            "mode": seg.get("mode"),
        })
    return result

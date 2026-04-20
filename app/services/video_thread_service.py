from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app.models import Merchant
from app.schemas.video_thread import CreateThreadRequest, ResumeThreadRequest
from app.services.video_graph.event_bus import BusEvent, get_event_bus, publish_event
from app.services.video_graph.graph import get_graph
from app.services.video_graph.view_state import FrontendViewState, map_graph_state_to_view

logger = logging.getLogger(__name__)

_RUNNING_TASKS: dict[str, asyncio.Task] = {}
_THREAD_OWNERS: dict[str, str] = {}


def _register_task(thread_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[thread_id] = task

    def _cleanup(_: asyncio.Task) -> None:
        _RUNNING_TASKS.pop(thread_id, None)

    task.add_done_callback(_cleanup)


def _is_running(thread_id: str) -> bool:
    task = _RUNNING_TASKS.get(thread_id)
    return task is not None and not task.done()


def _register_thread_owner(thread_id: str, store_id: str) -> None:
    _THREAD_OWNERS[thread_id] = store_id


def _owner_of(thread_id: str) -> Optional[str]:
    return _THREAD_OWNERS.get(thread_id)


async def _load_state_with_auth(thread_id: str, merchant: Merchant) -> dict:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        logger.warning("aget_state failed, fallback owner map: %s", thread_id, exc_info=True)
        snapshot = None

    if snapshot and snapshot.values:
        store_id = snapshot.values.get("shopify_store_id", "")
        if store_id != merchant.shopify_store_id:
            raise HTTPException(status_code=403, detail="forbidden")
        return snapshot.values

    owner_store_id = _owner_of(thread_id)
    if owner_store_id is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    if owner_store_id != merchant.shopify_store_id:
        raise HTTPException(status_code=403, detail="forbidden")

    return {
        "thread_id": thread_id,
        "shopify_store_id": owner_store_id,
        "current_step": "initializing",
    }


async def _run_graph_in_background(
    thread_id: str,
    *,
    initial_state: Optional[dict] = None,
    resume_command: Optional[Command] = None,
) -> None:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        if resume_command is not None:
            await graph.ainvoke(resume_command, config=config)
        else:
            await graph.ainvoke(initial_state or {}, config=config)
    except Exception as exc:
        exc_name = type(exc).__name__.lower()
        if "interrupt" in exc_name:
            logger.info("Graph paused at interrupt: %s", thread_id)
            return
        logger.exception("Graph run failed: %s", thread_id)
        await publish_event(thread_id, "error", {"message": f"工作流执行失败: {exc}"})


async def create_thread_task(payload: CreateThreadRequest, merchant: Merchant) -> dict:
    thread_id = str(uuid.uuid4())
    initial_state: dict = {
        "thread_id": thread_id,
        "user_input": payload.user_input,
        "trend": payload.trend.model_dump(mode="json"),
        "brand": payload.brand.model_dump(mode="json"),
        "product": payload.product.model_dump(mode="json"),
        "generation_mode": payload.generation_mode,
        "media_assets": payload.media_assets.model_dump(mode="json") if payload.media_assets else {},
        "config_params": payload.config_params.model_dump(mode="json") if payload.config_params else {},
        "shopify_store_id": merchant.shopify_store_id,
        "revision_count": 0,
        "current_step": "initializing",
    }

    _register_thread_owner(thread_id, merchant.shopify_store_id)

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        await graph.aupdate_state(config, initial_state)
    except Exception:
        logger.warning("aupdate_state failed, fallback owner map: %s", thread_id, exc_info=True)

    await publish_event(
        thread_id,
        "progress",
        {"progress": 2, "message": "任务初始化中…", "step": "initializing"},
    )

    task = asyncio.create_task(
        _run_graph_in_background(thread_id, initial_state=initial_state),
        name=f"video-thread:{thread_id}:create",
    )
    _register_task(thread_id, task)

    return {
        "thread_id": thread_id,
        "stream_url": f"/video-thread/{thread_id}/stream",
        "state_url": f"/video-thread/{thread_id}/state",
        "view": FrontendViewState(
            status="running",
            message="任务初始化中…",
            progress=2,
            current_step="initializing",
        ).model_dump(),
    }


async def resume_thread_task(
    thread_id: str,
    payload: ResumeThreadRequest,
    merchant: Merchant,
) -> dict:
    state = await _load_state_with_auth(thread_id, merchant)
    if state.get("current_step") in (None, "initializing"):
        raise HTTPException(status_code=409, detail="thread_not_ready_for_resume")
    if _is_running(thread_id):
        raise HTTPException(status_code=409, detail="thread_is_busy")

    human_input = {
        "action": payload.action,
        "edited_segments": [s.model_dump(exclude_none=True) for s in payload.edited_segments],
        "feedback": payload.feedback,
    }
    await publish_event(
        thread_id,
        "progress",
        {"progress": 60, "message": "已收到您的反馈，正在处理…", "step": "human_responded"},
    )
    task = asyncio.create_task(
        _run_graph_in_background(thread_id, resume_command=Command(resume=human_input)),
        name=f"video-thread:{thread_id}:resume",
    )
    _register_task(thread_id, task)
    return {
        "thread_id": thread_id,
        "accepted_action": payload.action,
        "view": FrontendViewState(
            status="running",
            message="已收到您的反馈，正在处理…",
            progress=60,
            current_step="human_responded",
        ).model_dump(),
    }


async def get_thread_view_state(thread_id: str, merchant: Merchant) -> dict:
    graph_state = await _load_state_with_auth(thread_id, merchant)
    view = map_graph_state_to_view(graph_state)
    return {"thread_id": thread_id, "running": _is_running(thread_id), "view": view.model_dump()}


async def stream_thread_events_response(
    thread_id: str,
    request: Request,
    merchant: Merchant,
) -> StreamingResponse:
    graph_state = await _load_state_with_auth(thread_id, merchant)

    bus = get_event_bus()
    queue = await bus.subscribe(thread_id)

    async def event_source():
        initial_view = map_graph_state_to_view(graph_state)
        yield _sse_pack("state", initial_view.model_dump())

        ping_interval = 15.0
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("SSE disconnected: %s", thread_id)
                    break
                try:
                    event: BusEvent = await asyncio.wait_for(queue.get(), timeout=ping_interval)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue

                yield _sse_pack(event.event, event.data)
                if event.event in ("done", "error"):
                    break
        finally:
            await bus.unsubscribe(thread_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_pack(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


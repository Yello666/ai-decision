from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.models import Merchant
from app.schemas.video_thread import (
    ConfigParamsInput,
    CreateThreadRequest,
    MediaAssetsInput,
    ResumeThreadRequest,
    UpdateThreadParamsRequest,
)
from app.services.video_graph.event_bus import BusEvent, get_event_bus, publish_event
from app.services.video_graph.graph import get_graph
from app.services.video_graph.state import DEFAULT_CONFIG
from app.services.video_graph.view_state import FrontendViewState, map_graph_state_to_view

logger = logging.getLogger(__name__)

_RUNNING_TASKS: dict[str, asyncio.Task] = {}

_THREAD_OWNERS: dict[str, str] = {} #标记每一个thread_id所属的store_id

# 允许修改全局视频参数的阶段。
# 设计原则：parse_intent 尚未完成前不能改（parsed_* 还未生成）；
# 一旦进入 assemble_and_submit 及其后续阶段，任务已落库/已提交，改参数无意义。
_PARAM_EDITABLE_STEPS = frozenset(
    {
        "parse_intent_done",
        "plan_script_done",
        "waiting_human",
    }
)


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


def _build_param_patch(
    current_state: dict,
    *,
    config_params: Optional[ConfigParamsInput],
    media_assets: Optional[MediaAssetsInput],
    generation_mode: Optional[str],
) -> dict:
    """根据用户传入的覆盖项，生成写回 Graph state 的 patch 字典。

    - `config_params` 做字段级 merge（仅覆盖显式传入的字段），并同步更新 `parsed_config`。
    - `media_assets` 整体替换，同步更新 `parsed_media`。
    - `generation_mode` 同时写入 `generation_mode` 与 `parsed_mode`。
    - 切到 image_to_video / frame_interpolation 时，若新 state 下仍无图片，直接抛 400。
    """

    patch: dict = {}

    if config_params is not None:
        override = config_params.model_dump(mode="json", exclude_none=True)
        if override:
            patch["config_params"] = {
                **(current_state.get("config_params") or {}),
                **override,
            }
            patch["parsed_config"] = {
                **DEFAULT_CONFIG,
                **(current_state.get("parsed_config") or {}),
                **override,
            }

    if media_assets is not None:
        media = media_assets.model_dump(mode="json", exclude_none=True)
        patch["media_assets"] = media
        patch["parsed_media"] = {
            "ref_image_urls": media.get("ref_image_urls") or [],
            "first_frame_url": media.get("first_frame_url") or "",
            "last_frame_url": media.get("last_frame_url") or "",
        }

    if generation_mode is not None:
        patch["generation_mode"] = generation_mode
        patch["parsed_mode"] = generation_mode

    if not patch:
        return patch

    effective_mode = (
        patch.get("parsed_mode")
        or current_state.get("parsed_mode")
        or current_state.get("generation_mode")
        or "text_to_video"
    )
    if effective_mode in ("image_to_video", "frame_interpolation"):
        effective_media = patch.get("parsed_media") or current_state.get("parsed_media") or {}
        if not effective_media:
            fallback = current_state.get("media_assets") or {}
            effective_media = {
                "ref_image_urls": fallback.get("ref_image_urls") or [],
                "first_frame_url": fallback.get("first_frame_url") or "",
                "last_frame_url": fallback.get("last_frame_url") or "",
            }
        has_images = bool(
            effective_media.get("ref_image_urls") or effective_media.get("first_frame_url")
        )
        if not has_images:
            raise HTTPException(
                status_code=400,
                detail=f"mode_{effective_mode}_requires_images",
            )

    return patch


async def _apply_param_overrides(
    thread_id: str,
    current_state: dict,
    *,
    config_params: Optional[ConfigParamsInput],
    media_assets: Optional[MediaAssetsInput],
    generation_mode: Optional[str],
) -> dict:
    """在 Graph state 上应用参数覆盖；返回写入 Graph 的 patch（可能为空）。

    调用方应先确认 thread 未在运行，再调用本函数。
    """
    patch = _build_param_patch(
        current_state,
        config_params=config_params,
        media_assets=media_assets,
        generation_mode=generation_mode,
    )
    if not patch:
        return patch

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    await graph.aupdate_state(config, patch)

    await publish_event(
        thread_id,
        "params_updated",
        {
            "config_params": patch.get("parsed_config") or patch.get("config_params"),
            "generation_mode": patch.get("parsed_mode"),
            "media_assets": patch.get("parsed_media"),
        },
    )
    return patch


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
    except GraphInterrupt:
        logger.info("Graph paused at interrupt: %s", thread_id)
        return
    except Exception as exc:
        logger.exception("Graph run failed: %s", thread_id)
        await publish_event(thread_id, "error", {"message": f"工作流执行失败: {exc}"})


async def create_thread_task(payload: CreateThreadRequest, merchant: Merchant) -> dict:
    # 1.初始化thread_id和state
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
        "revision_count": 0, #视频脚本最大修改轮次（10次，超过这个次数，将提示用户已达最大上下文？让用户直接编辑然后执行）
        "current_step": "initializing",
    }

    _register_thread_owner(thread_id, merchant.shopify_store_id)

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        # a代表async异步
        await graph.aupdate_state(config, initial_state)
    except Exception:
        logger.warning("aupdate_state failed, fallback owner map: %s", thread_id, exc_info=True)

    # 这里不需要向前端推送，因为只能拿到thread_id之后才可以建立SSE连接
    # 在这个接口结束之后，才会拿到thread_id，建立SSE连接
    # await publish_event(
    #     thread_id,
    #     "progress",
    #     {"progress": 2, "message": "任务初始化中…", "step": "initializing"},
    # )

    task = asyncio.create_task(
        _run_graph_in_background(thread_id, initial_state=initial_state),
        name=f"video-thread:{thread_id}:create",
    )
    _register_task(thread_id, task)

    return {
        "thread_id": thread_id,
        # 因为没有SSE推送，所以在这里返回view。
        "view": FrontendViewState(
            status="running",
            message="任务初始化中…",
            progress=2,
            current_step="initializing",
        ).model_dump(),
    }

# 注入人类决策，恢复Graph执行
async def resume_thread_task(
    thread_id: str,
    payload: ResumeThreadRequest,
    merchant: Merchant,
) -> dict:
    state = await _load_state_with_auth(thread_id, merchant)
    if state.get("current_step") in (None, "initializing"):
        raise HTTPException(status_code=409, detail="thread_not_ready_for_resume")
    # 防止task还没有结束，就调用恢复接口
    if _is_running(thread_id):
        raise HTTPException(status_code=409, detail="thread_is_busy")

    has_param_override = (
        payload.config_params is not None
        or payload.media_assets is not None
        or payload.generation_mode is not None
    )
    # 允许修改视频参数
    if has_param_override:
        if state.get("current_step") not in _PARAM_EDITABLE_STEPS:
            raise HTTPException(status_code=409, detail="params_not_editable_in_current_step")
        await _apply_param_overrides(
            thread_id,
            state,
            config_params=payload.config_params,
            media_assets=payload.media_assets,
            generation_mode=payload.generation_mode,
        )

    human_input = {
        "human_action": payload.action,
        "human_edited_segments": [s.model_dump(exclude_none=True) for s in payload.edited_segments],
        "human_feedback": payload.feedback,
    }
    # 再次新建task执行graph，因为之前的task已经停止并回收了，需要重新开始执行
    task = asyncio.create_task(
        _run_graph_in_background(thread_id, resume_command=Command(resume=human_input)), 
        name=f"video-thread:{thread_id}:resume",
    )
    # resume=human_input是将上面的human_input作为intterupt函数的返回值
    _register_task(thread_id, task)
    return {
        "thread_id": thread_id,
        "human_action": payload.action,
        "view": FrontendViewState(
            status="running",
            message="已收到您的反馈，正在处理…",
            progress=60,
            current_step="human_responded",
        ).model_dump(),
    }


async def update_thread_params(
    thread_id: str,
    payload: UpdateThreadParamsRequest,
    merchant: Merchant,
) -> dict:
    """仅修改视频全局参数、不推进 Graph。返回当前视图态。

    限制条件：
      1. 会话必须属于当前商户（_load_state_with_auth 负责校验）；
      2. 会话未在后台执行中；
      3. 当前步骤处于可编辑阶段（未提交 Seedance 任务前）。
    """
    state = await _load_state_with_auth(thread_id, merchant)
    if _is_running(thread_id):
        raise HTTPException(status_code=409, detail="thread_is_busy")
    if state.get("current_step") not in _PARAM_EDITABLE_STEPS:
        raise HTTPException(status_code=409, detail="params_not_editable_in_current_step")

    if (
        payload.config_params is None
        and payload.media_assets is None
        and payload.generation_mode is None
    ):
        raise HTTPException(status_code=400, detail="no_params_provided")

    await _apply_param_overrides(
        thread_id,
        state,
        config_params=payload.config_params,
        media_assets=payload.media_assets,
        generation_mode=payload.generation_mode,
    )

    refreshed = await _load_state_with_auth(thread_id, merchant)
    view = map_graph_state_to_view(refreshed)
    return {
        "thread_id": thread_id,
        "view": view.model_dump(),
        "config_params": refreshed.get("parsed_config") or refreshed.get("config_params") or {},
        "generation_mode": refreshed.get("parsed_mode") or refreshed.get("generation_mode"),
        "media_assets": refreshed.get("parsed_media") or refreshed.get("media_assets") or {},
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


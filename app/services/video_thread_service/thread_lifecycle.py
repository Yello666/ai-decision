from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.db.mysql import SessionLocal
from app.models import Generation, Merchant, VideoThread
from app.schemas.video_thread import (
    ConfigParamsInput,
    CreateThreadRequest,
    MediaAssetsInput,
    ProductForPrompt,
    ResumeThreadRequest,
    ThreadHistoryResponse,
    ThreadHistoryTurn,
    UpdateThreadParamsRequest,
    VideoThreadListItem,
    VideoThreadListResponse,
)
from app.services.video_thread_service.video_graph.event_bus import BusEvent, get_event_bus, publish_event
from app.services.video_thread_service.video_graph.graph import get_graph
from app.services.video_thread_service.video_graph.state import DEFAULT_CONFIG
from app.services.video_thread_service.video_graph.view_state import (
    FrontendViewState,
    format_segments_for_view,
    map_graph_state_to_view,
)

logger = logging.getLogger(__name__)

_RUNNING_TASKS: dict[str, asyncio.Task] = {}

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

# 列表页标题截断长度（与 VideoThread.title 列长度对齐）
_TITLE_MAX_LEN = 100


def _register_task(thread_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[thread_id] = task

    def _cleanup(_: asyncio.Task) -> None:
        _RUNNING_TASKS.pop(thread_id, None)

    task.add_done_callback(_cleanup)


def _is_running(thread_id: str) -> bool:
    task = _RUNNING_TASKS.get(thread_id)
    return task is not None and not task.done()


# ─────────────────────────────────────────────────────────────
# video_threads 索引表：增删改查助手
# ─────────────────────────────────────────────────────────────
# 以下方法均为同步（SQLAlchemy sync Session），在 async 调用点用 asyncio.to_thread 调度。


def _derive_status(state: dict, *, workflow_ended: bool) -> str:
    """根据 graph state + 是否到达 END 推导索引表的 status。"""
    error_msg = state.get("error")
    current_step = state.get("current_step") or ""
    if error_msg or current_step == "error":
        return "error"
    if workflow_ended:
        return "finished"
    if current_step == "waiting_human":
        return "waiting_human"
    return "running"


def _pick_thumbnail(
    product: Optional[dict],
    media_assets: Optional[dict],
) -> Optional[str]:
    """创建 thread 时确定列表页缩略图。

    优先级：
      1. 商品主图 ``product.image_url``（最贴合"这个 thread 在讲什么")；
      2. 首张参考图 ``media_assets.ref_image_urls[0]``。
    都没有则返回 None（前端自行兜底占位图）。
    """
    if product:
        img = (product.get("image_url") or "").strip()
        if img:
            return img
    if media_assets:
        refs = media_assets.get("ref_image_urls") or []
        if refs:
            first_ref = str(refs[0]).strip()
            if first_ref:
                return first_ref
    return None


def _build_product_for_prompt(payload: CreateThreadRequest) -> ProductForPrompt:
    """将商品/规格选择压缩成 LLM 剧情规划所需的最小上下文。"""
    selected_variant = payload.product.variants[0] if payload.product.variants else None
    if selected_variant is None:
        return ProductForPrompt(
            name=payload.product.name,
            description=payload.product.description,
            size_description=payload.product.size_description or "",
            price=payload.product.price,
            image_url=payload.product.image_url,
        )

    return ProductForPrompt(
        name=selected_variant.name,
        description=payload.product.description,
        size_description=payload.product.size_description or "",
        price=selected_variant.price,
        image_url=selected_variant.image_url or payload.product.image_url,
    )


def _merge_product_image_into_media(media_assets: Optional[dict], product_for_prompt: dict) -> dict:
    """商品图作为 [image1]，用户上传参考图顺延，保证标签与 payload 顺序一致。"""
    media = dict(media_assets or {})
    refs = [str(url).strip() for url in media.get("ref_image_urls", []) if str(url).strip()]
    product_image = str(product_for_prompt.get("image_url") or "").strip()
    if product_image and product_image not in refs:
        refs = [product_image, *refs]
    media["ref_image_urls"] = refs
    media["reference_video_urls"] = media.get("reference_video_urls") or []
    media["reference_audio_urls"] = media.get("reference_audio_urls") or []
    return media


def _db_insert_thread(
    thread_id: str,
    store_id: str,
    user_input: str,
    thumbnail_url: Optional[str],
) -> None:
    """thread 创建时 INSERT 索引行；幂等：已存在则跳过（防止重复提交）。"""
    db = SessionLocal()
    try:
        exists = db.query(VideoThread).filter(VideoThread.thread_id == thread_id).first()
        if exists is not None:
            return
        title = (user_input or "").strip()[:_TITLE_MAX_LEN] or None
        row = VideoThread(
            thread_id=thread_id,
            shopify_store_id=store_id,
            status="running",
            current_step="initializing",
            title=title,
            user_input=user_input or None,
            thumbnail_url=thumbnail_url,
            revision_count=0,
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to insert video_thread row: %s", thread_id)
    finally:
        db.close()


def _db_update_thread_snapshot(
    thread_id: str,
    *,
    status: str,
    current_step: Optional[str],
    revision_count: Optional[int],
    error_message: Optional[str],
    completed: bool,
) -> None:
    """工作流状态变化时 UPDATE 索引行；仅更新已存在行。"""
    db = SessionLocal()
    try:
        row = db.query(VideoThread).filter(VideoThread.thread_id == thread_id).first()
        if row is None:
            logger.warning("video_thread row missing on update: %s", thread_id)
            return
        row.status = status
        if current_step:
            row.current_step = current_step
        if revision_count is not None:
            row.revision_count = revision_count
        if error_message:
            row.error_message = error_message
        if completed:
            row.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to update video_thread row: %s", thread_id)
    finally:
        db.close()


def _db_get_thread_owner(thread_id: str) -> Optional[VideoThread]:
    """返回 thread 的 VideoThread 行（用于归属校验与列表页兜底展示）。"""
    db = SessionLocal()
    try:
        return (
            db.query(VideoThread).filter(VideoThread.thread_id == thread_id).first()
        )
    finally:
        db.close()


def _db_list_threads(
    store_id: str,
    status: Optional[str],
    limit: int,
    offset: int,
) -> tuple[int, list[VideoThread]]:
    db = SessionLocal()
    try:
        q = db.query(VideoThread).filter(VideoThread.shopify_store_id == store_id)
        if status:
            q = q.filter(VideoThread.status == status)
        total = q.count()
        rows = (
            q.order_by(VideoThread.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return total, rows
    finally:
        db.close()


def _db_list_generation_urls(thread_ids: list[str]) -> dict[str, list[str]]:
    """按 thread_id 批量取该 thread 下所有 succeeded 的视频 URL。

    - 仅选取 type=video & status=succeeded & result_url IS NOT NULL；
    - 结果按 generations.created_at 升序，保持与 segment 提交顺序一致；
    - 未命中的 thread_id 不会出现在返回字典里。
    """
    if not thread_ids:
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(Generation.thread_id, Generation.result_url)
            .filter(
                Generation.thread_id.in_(thread_ids),
                Generation.type == "video",
                Generation.status == "succeeded",
                Generation.result_url.isnot(None),
            )
            .order_by(Generation.created_at.asc())
            .all()
        )
        result: dict[str, list[str]] = {}
        for tid, url in rows:
            if not tid or not url:
                continue
            result.setdefault(tid, []).append(url)
        return result
    finally:
        db.close()


def _db_get_generation_urls(thread_id: str) -> list[str]:
    """单个 thread 的 succeeded 视频 URL 列表（详情页用）。"""
    return _db_list_generation_urls([thread_id]).get(thread_id, [])


async def _persist_thread_snapshot(thread_id: str, *, workflow_ended: bool) -> None:
    """读当前 graph state，把 status / current_step / revision_count 同步到索引表。"""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await graph.aget_state(config)
        state: dict[str, Any] = snapshot.values if snapshot else {}
    except Exception:
        logger.warning("aget_state failed during persist: %s", thread_id, exc_info=True)
        state = {}

    status = _derive_status(state, workflow_ended=workflow_ended)
    await asyncio.to_thread(
        _db_update_thread_snapshot,
        thread_id,
        status=status,
        current_step=state.get("current_step"),
        revision_count=state.get("revision_count"),
        error_message=state.get("error"),
        completed=status in ("finished", "error"),
    )


async def _load_state_with_auth(thread_id: str, merchant: Merchant) -> dict:
    """读取 graph state 并做归属校验。

    归属兜底顺序：
    1) graph state 中的 ``shopify_store_id``；
    2) MySQL ``video_threads`` 索引行；
    3) 以上都没有 → 404。
    """
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        logger.warning("aget_state failed, fallback to DB: %s", thread_id, exc_info=True)
        snapshot = None

    if snapshot and snapshot.values:
        store_id = snapshot.values.get("shopify_store_id", "")
        if store_id and store_id != merchant.shopify_store_id:
            raise HTTPException(status_code=403, detail="forbidden")
        if store_id:
            return snapshot.values
        # state 里没有 store_id（刚创建未落态），走 DB 兜底

    row = await asyncio.to_thread(_db_get_thread_owner, thread_id)
    if row is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    if row.shopify_store_id != merchant.shopify_store_id:
        raise HTTPException(status_code=403, detail="forbidden")

    if snapshot and snapshot.values:
        # state 存在但无 store_id：用 DB 兜底的值回填
        values = dict(snapshot.values)
        values["shopify_store_id"] = row.shopify_store_id
        return values

    # state 彻底缺失（极早期或已被清理）：构造最小回退状态
    return {
        "thread_id": thread_id,
        "shopify_store_id": row.shopify_store_id,
        "current_step": row.current_step or "initializing",
        "user_input": row.user_input or "",
        "revision_count": row.revision_count or 0,
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
    - 切到 multimodal_reference 时，若新 state 下仍无图片或视频，直接抛 400。
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
        media = _merge_product_image_into_media(
            media_assets.model_dump(mode="json", exclude_none=True),
            current_state.get("product_for_prompt") or {},
        )
        ref_image_urls = list(media.get("ref_image_urls") or [])
        patch["media_assets"] = media
        patch["parsed_media"] = {
            "ref_image_urls": ref_image_urls,
            "reference_video_urls": media.get("reference_video_urls") or [],
            "reference_audio_urls": media.get("reference_audio_urls") or [],
        }
        if (
            patch["parsed_media"]["ref_image_urls"]
            or patch["parsed_media"]["reference_video_urls"]
            or patch["parsed_media"]["reference_audio_urls"]
        ):
            patch["generation_mode"] = "multimodal_reference"
            patch["parsed_mode"] = "multimodal_reference"

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
    if effective_mode == "multimodal_reference":
        effective_media = patch.get("parsed_media") or current_state.get("parsed_media") or {}
        if not effective_media:
            fallback = current_state.get("media_assets") or {}
            ref_image_urls = list(fallback.get("ref_image_urls") or [])
            effective_media = {
                "ref_image_urls": ref_image_urls,
                "reference_video_urls": fallback.get("reference_video_urls") or [],
                "reference_audio_urls": fallback.get("reference_audio_urls") or [],
            }
        has_image_or_video = bool(
            effective_media.get("ref_image_urls")
            or effective_media.get("reference_video_urls")
        )
        if not has_image_or_video:
            raise HTTPException(
                status_code=400,
                detail="mode_multimodal_reference_requires_image_or_video",
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
        # interrupt 处于中间态（waiting_human），同步一下状态供列表页展示
        await _persist_thread_snapshot(thread_id, workflow_ended=False)
        return
    except Exception as exc:
        logger.exception("Graph run failed: %s", thread_id)
        await publish_event(thread_id, "error", {"message": f"工作流执行失败: {exc}"})
        await _persist_thread_snapshot(thread_id, workflow_ended=False)
        return

    # 正常到达 END（respond → END）：标记 finished，保留 checkpoint 作为历史对话记录
    await _persist_thread_snapshot(thread_id, workflow_ended=True)


async def create_thread_task(payload: CreateThreadRequest, merchant: Merchant) -> dict:
    # 1.初始化thread_id和state
    thread_id = str(uuid.uuid4())
    product = payload.product.model_dump(mode="json")
    product_for_prompt = _build_product_for_prompt(payload).model_dump(mode="json")
    media_assets = _merge_product_image_into_media(
        payload.media_assets.model_dump(mode="json") if payload.media_assets else None,
        product_for_prompt,
    )
    initial_state: dict = {
        "thread_id": thread_id,
        "user_input": payload.user_input,
        "trend": payload.trend.model_dump(mode="json"),
        "brand": payload.brand.model_dump(mode="json") if payload.brand else {},
        "product": product,
        "product_for_prompt": product_for_prompt,
        "generation_mode": payload.generation_mode,
        "media_assets": media_assets,
        "config_params": payload.config_params.model_dump(mode="json") if payload.config_params else {},
        "shopify_store_id": merchant.shopify_store_id,
        "is_standalone_merchant": (merchant.account_type == "standalone"),
        "revision_count": 0, #视频脚本最大修改轮次（10次，超过这个次数，将提示用户已达最大上下文？让用户直接编辑然后执行）
        "current_step": "initializing",
    }

    # 创建时确定列表页缩略图：商品主图 > 首帧 > 首张参考图
    thumbnail = _pick_thumbnail(
        product=product,
        media_assets=media_assets,
    )

    # 立即写 video_threads 索引行，作为列表查询的权威数据源与归属校验依据
    await asyncio.to_thread(
        _db_insert_thread,
        thread_id,
        merchant.shopify_store_id,
        payload.user_input or "",
        thumbnail,
    )

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
    # 标记状态回到 running，列表页能即时反映"用户已确认/反馈，正在推进"
    await asyncio.to_thread(
        _db_update_thread_snapshot,
        thread_id,
        status="running",
        current_step="human_responded",
        revision_count=None,
        error_message=None,
        completed=False,
    )
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


# ─────────────────────────────────────────────────────────────
# 历史会话列表查询
# ─────────────────────────────────────────────────────────────


async def list_video_threads(
    merchant: Merchant,
    *,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> VideoThreadListResponse:
    """按 store_id 分页列出该商户的 video thread 历史。

    - 列表只返回轻量索引字段（不含 segments / 完整 state），按 created_at 倒序；
    - 前端点进某条会话后，再调 ``GET /video-thread/{thread_id}/state`` 拿详情；
    - ``status`` 支持按 running / waiting_human / finished / error 过滤。
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total, rows = await asyncio.to_thread(
        _db_list_threads,
        merchant.shopify_store_id,
        status,
        limit,
        offset,
    )

    # 批量查询当前页 thread 的最终视频 URL，避免 N+1
    thread_ids = [r.thread_id for r in rows]
    url_map = await asyncio.to_thread(_db_list_generation_urls, thread_ids)

    items: list[VideoThreadListItem] = []
    for r in rows:
        item = VideoThreadListItem.model_validate(r)
        item.final_video_urls = url_map.get(r.thread_id, [])
        items.append(item)
    return VideoThreadListResponse(items=items, total=total, limit=limit, offset=offset)


# ─────────────────────────────────────────────────────────────
# 历史会话详情（replay 对话过程）
# ─────────────────────────────────────────────────────────────
# 借助 LangGraph ``aget_state_history`` 把所有 checkpoint 快照拉回来，按时间正序
# 解析出对前端有意义的"轮次"（user_input → assistant_draft → user_action → …）。
# 设计要点：
#   - checkpoint 数量可能很多；Postgres 持久化后 metadata.writes 不可用，
#     因此用 values（current_step / human_action / script_segments 签名）推断 turn；
#   - segments 返回给前端前统一用 ``format_segments_for_view`` 脱敏；
#   - 即使某些 checkpoint 已被清理（TTL 到期），剩下的 turn 仍可渲染时间线。


def _snapshot_created_at(snapshot: Any) -> Optional[datetime]:
    """拿到 checkpoint 的创建时间：优先用 LangGraph 给的 ISO 字符串，再退回 UUIDv6。"""
    created_raw = getattr(snapshot, "created_at", None)
    if created_raw:
        try:
            if isinstance(created_raw, datetime):
                return created_raw
            return datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass

    cfg = getattr(snapshot, "config", None) or {}
    cid = (cfg.get("configurable") or {}).get("checkpoint_id")
    if cid:
        from app.db.postgres import _uuid6_time  # 局部导入，避免循环依赖

        return _uuid6_time(cid)
    return None


def _checkpoint_id_of(snapshot: Any) -> Optional[str]:
    cfg = getattr(snapshot, "config", None) or {}
    return (cfg.get("configurable") or {}).get("checkpoint_id")


def _draft_fingerprint(raw_segments: list[Any]) -> str:
    """用于区分「新一版分镜草稿」的稳定签名（Postgres 持久化后无 metadata.writes）。"""
    normalized: list[dict[str, Any]] = []
    for s in raw_segments or []:
        if not isinstance(s, dict):
            continue
        normalized.append(
            {
                "segment_id": s.get("segment_id"),
                "description": s.get("description"),
                "description_zh": s.get("description_zh"),
                "duration": s.get("duration"),
                "mode": s.get("mode"),
            }
        )
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _build_turn_from_snapshot(
    snapshot: Any,
    *,
    prev_values: Optional[dict[str, Any]],
    state_emitted_user_input: bool,
    submitted_emitted: bool,
    last_draft_fingerprint: Optional[str],
) -> tuple[Optional[ThreadHistoryTurn], bool, bool, Optional[str]]:
    """将单个 snapshot 映射为 0 个或 1 个历史 turn。

    Postgres checkpointer 落库时会剥掉 ``metadata.writes``，因此草稿 / 决策 / 提交
    仅靠 ``values``（``current_step``、``human_action``、``script_segments`` 等）推断。

    返回 ``(turn, new_state_emitted_user_input, new_submitted_emitted, new_last_draft_fingerprint)``。
    """
    values: dict = getattr(snapshot, "values", None) or {}
    if not isinstance(values, dict):
        values = {}
    metadata: dict = getattr(snapshot, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    source = metadata.get("source")
    prev_step = (prev_values or {}).get("current_step") if prev_values else None

    created_at = _snapshot_created_at(snapshot)
    checkpoint_id = _checkpoint_id_of(snapshot)
    step = values.get("current_step")
    revision_count = values.get("revision_count")

    base_kwargs: dict[str, Any] = {
        "checkpoint_id": checkpoint_id,
        "created_at": created_at,
        "step": step,
        "revision_count": revision_count,
    }

    last_fp = last_draft_fingerprint

    # 1) 第一条 user_input：由 create_thread_task 里 aupdate_state 写入，source="update"
    if not state_emitted_user_input and source in ("input", "update"):
        user_input = (values.get("user_input") or "").strip()
        if user_input:
            turn = ThreadHistoryTurn(
                kind="user_input",
                user_input=user_input,
                **base_kwargs,
            )
            return turn, True, submitted_emitted, last_fp

    # 2) 用户决策：resume 后 human_interrupt 返回 human_responded + human_action
    action = values.get("human_action")
    if (
        step == "human_responded"
        and prev_step != "human_responded"
        and action in ("approve", "edit", "feedback")
    ):
        turn = ThreadHistoryTurn(
            kind="user_action",
            action=action,
            human_feedback=values.get("human_feedback") or None,
            human_edited_segments=values.get("human_edited_segments") or None,
            **base_kwargs,
        )
        return turn, state_emitted_user_input, submitted_emitted, last_fp

    # 3) LLM / 编辑产出的一版可审阅草稿（plan_script、apply_edit、revise_script 均落在此 step）
    raw_segments = values.get("script_segments") or []
    if step == "plan_script_done" and isinstance(raw_segments, list) and raw_segments:
        fp = _draft_fingerprint(raw_segments)
        if fp != last_fp:
            config = values.get("parsed_config") or {}
            language = (config.get("language") if isinstance(config, dict) else None) or "zh"
            turn = ThreadHistoryTurn(
                kind="assistant_draft",
                segments=format_segments_for_view(raw_segments, language),
                total_duration=values.get("total_duration"),
                execution_strategy=values.get("execution_strategy"),
                **base_kwargs,
            )
            return turn, state_emitted_user_input, submitted_emitted, fp

    # 4) 已向 Seedance 提交（assemble_and_submit 返回值）
    if not submitted_emitted and step == "submitted":
        turn = ThreadHistoryTurn(kind="submitted", **base_kwargs)
        return turn, state_emitted_user_input, True, last_fp

    return None, state_emitted_user_input, submitted_emitted, last_fp


async def get_thread_conversation_history(
    thread_id: str,
    merchant: Merchant,
) -> ThreadHistoryResponse:
    """回放一个 video thread 从创建到当前的完整对话过程。

    - 先做归属校验（借用 ``_load_state_with_auth``）；
    - 通过 LangGraph ``aget_state_history`` 拉取全部 checkpoint，按时间正序遍历；
    - 每个 turn 只下发前端需要的字段，segments 已按语言脱敏。
    """
    current_state = await _load_state_with_auth(thread_id, merchant)

    # 读一份 MySQL 索引行，用于补齐列表页的 title / thumbnail_url 等展示字段
    row = await asyncio.to_thread(_db_get_thread_owner, thread_id)
    final_urls = await asyncio.to_thread(_db_get_generation_urls, thread_id)

    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshots: list[Any] = []
    try:
        async for snap in graph.aget_state_history(config):
            snapshots.append(snap)
    except Exception:
        logger.warning(
            "aget_state_history failed: %s", thread_id, exc_info=True
        )

    # aget_state_history 返回倒序（最新在前），这里反转成正序便于按时间拼 timeline
    snapshots.reverse()

    turns: list[ThreadHistoryTurn] = []
    state_emitted_user_input = False
    submitted_emitted = False
    last_draft_fingerprint: Optional[str] = None
    prev_values: Optional[dict[str, Any]] = None
    for snap in snapshots:
        turn, state_emitted_user_input, submitted_emitted, last_draft_fingerprint = (
            _build_turn_from_snapshot(
                snap,
                prev_values=prev_values,
                state_emitted_user_input=state_emitted_user_input,
                submitted_emitted=submitted_emitted,
                last_draft_fingerprint=last_draft_fingerprint,
            )
        )
        if turn is not None:
            turns.append(turn)
        v = getattr(snap, "values", None) or {}
        prev_values = v if isinstance(v, dict) else {}

    # 兜底：如果 checkpoint 已被 TTL 清理、或刚创建还没落 state，而 MySQL 索引行还在，
    # 至少把 user_input 作为第一条 turn 还原出来，保证前端不至于空白。
    if not turns and row and (row.user_input or "").strip():
        turns.append(
            ThreadHistoryTurn(
                kind="user_input",
                user_input=row.user_input,
                created_at=row.created_at,
                step=row.current_step,
                revision_count=row.revision_count,
            )
        )

    status = _derive_status(
        current_state,
        workflow_ended=(current_state.get("current_step") in ("done", "submitted")),
    )

    product_raw = current_state.get("product")
    product_for_prompt_raw = current_state.get("product_for_prompt")
    product_snapshot = dict(product_raw) if isinstance(product_raw, dict) else None
    product_for_prompt_snapshot = (
        dict(product_for_prompt_raw) if isinstance(product_for_prompt_raw, dict) else None
    )

    return ThreadHistoryResponse(
        thread_id=thread_id,
        status=status,  # type: ignore[arg-type]
        current_step=current_state.get("current_step"),
        revision_count=current_state.get("revision_count"),
        title=row.title if row else None,
        thumbnail_url=row.thumbnail_url if row else None,
        final_video_urls=final_urls,
        turns=turns,
        product=product_snapshot,
        product_for_prompt=product_for_prompt_snapshot,
    )


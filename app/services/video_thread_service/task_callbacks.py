from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.redis import get_redis_client
from app.models import Generation
from app.schemas.video_thread import VideoTaskCallbackRequest
from app.services.notification_service import publish_generation_status

logger = logging.getLogger(__name__)

WS_CLOSE_NORMAL = 1000
WS_CLOSE_CLIENT_ACTIVITY_TIMEOUT = 4002
WS_CLOSE_INVALID_TOKEN = 4001
WS_CLOSE_INVALID_CLIENT_JSON = 1008


async def process_video_task_callback(payload: VideoTaskCallbackRequest, db: Session) -> dict:
    task_id = payload.id
    status = payload.status
    logger.info("收到方舟回调: task_id=%s, status=%s", task_id, status)

    video_url = payload.content.video_url if payload.content and payload.content.video_url else None
    error_msg = payload.error.message if payload.error and payload.error.message else None

    gen = handle_video_task_callback(
        db=db,
        task_id=task_id,
        status=status,
        video_url=video_url,
        error_message=error_msg,
        raw_payload=payload.model_dump(),
    )
    if gen is None:
        logger.warning("回调处理完成但未找到匹配记录: task_id=%s", task_id)
        return {"received": True, "matched": False}

    await publish_generation_status(
        store_id=gen.shopify_store_id,
        generation_id=gen.id,
        status=gen.status,
        video_url=gen.result_url,
        error_message=gen.error_message,
    )

    if gen.thread_id:
        await _patch_task_result_status_in_graph(
            gen.thread_id,
            task_id,
            status=gen.status,
            video_url=gen.result_url or video_url or "",
            error_message=gen.error_message or error_msg or "",
        )
        try:
            from app.services.video_thread_service.video_graph.event_bus import publish_event

            await publish_event(gen.thread_id, "video_result_updated", {
                "task_id": task_id,
                "generation_id": gen.id,
                "status": gen.status,
                "video_url": gen.result_url,
                "error_message": gen.error_message,
            })
        except Exception:
            logger.warning("推送视频审阅结果更新失败: thread_id=%s", gen.thread_id)

    if status == "succeeded":
        continuity_image_url = payload.content.last_frame_url if payload.content and payload.content.last_frame_url else ""
        if gen.thread_id and payload.content and payload.content.last_frame_url:
            await _patch_task_result_last_frame_in_graph(
                gen.thread_id,
                task_id,
                payload.content.last_frame_url,
            )
        await continue_sequential_chain(db, task_id, continuity_image_url)
    elif status in {"failed", "expired", "cancelled"}:
        await fail_remaining_sequential_chain(db, task_id, status)

    if gen.thread_id:
        await _maybe_resume_video_review(gen.thread_id)

    logger.info("回调处理完成: task_id=%s, generation_id=%s, status=%s", task_id, gen.id, gen.status)
    return {"received": True, "matched": True, "generation_id": gen.id}



# --------------------------
# 回调处理
# --------------------------
def get_generation_by_external_id(db: Session, external_id: str) -> Optional[Generation]:
    """通过外部任务 ID（火山方舟 task_id）查找 Generation 记录。"""
    return (
        db.query(Generation)
        .filter(Generation.external_id == external_id)
        .first()
    )


def handle_video_task_callback(
    db: Session,
    task_id: str,
    status: str,
    video_url: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_payload: Optional[dict] = None,
) -> Optional[Generation]:
    """
    处理方舟平台视频任务回调，更新 Generation 记录状态。
    幂等设计：已处于终态（succeeded/failed/expired）的记录不再更新。
    """
    gen = get_generation_by_external_id(db, task_id)
    if not gen:
        logger.warning("回调找不到对应的 Generation 记录, task_id=%s", task_id)
        return None

    terminal_statuses = {"succeeded", "failed", "expired"}
    if gen.status in terminal_statuses:
        logger.info(
            "Generation(id=%s) 已处于终态 '%s'，忽略重复回调 status='%s'",
            gen.id, gen.status, status,
        )
        return gen

    logger.info(
        "回调更新 Generation(id=%s): %s -> %s, task_id=%s",
        gen.id, gen.status, status, task_id,
    )
    gen.status = status

    if status == "succeeded":
        if video_url:
            gen.result_url = video_url
        logger.info(
            "任务成功，持久化完整数据: Generation(id=%s), video_url=%s",
            gen.id, video_url,
        )
    elif status == "failed":
        gen.error_message = error_message or "任务失败"
        logger.warning(
            "任务失败: Generation(id=%s), error=%s",
            gen.id, gen.error_message,
        )
    elif status == "expired":
        gen.error_message = error_message or "任务超时"
        logger.warning(
            "任务超时: Generation(id=%s)", gen.id,
        )

    db.commit()
    db.refresh(gen)
    return gen



async def _update_task_results_in_graph(thread_id: str, new_result: dict) -> None:
    """将新的 task_result 追加到 graph state，便于查询时看到所有段的 task_id。"""
    try:
        from app.services.video_thread_service.video_graph.graph import get_graph

        graph = get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await graph.aget_state(config)
        current_results = list((snapshot.values or {}).get("task_results", []))
        current_results.append(new_result)
        await graph.aupdate_state(config, {"task_results": current_results})
    except Exception:
        logger.warning("更新 graph state task_results 失败: thread_id=%s", thread_id)


async def _patch_task_result_last_frame_in_graph(
    thread_id: str,
    task_id: str,
    last_frame_url: str,
) -> None:
    """任务成功回调时，把该 task 的尾帧输出 URL 写回 graph state 中对应 task_result。"""
    url = (last_frame_url or "").strip()
    if not thread_id or not task_id or not url:
        return
    try:
        from app.services.video_thread_service.video_graph.graph import get_graph

        graph = get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await graph.aget_state(config)
        current_results = list((snapshot.values or {}).get("task_results", []))
        patched = False
        new_results: list[dict] = []
        for tr in current_results:
            row = dict(tr)
            if row.get("task_id") == task_id:
                row["last_frame_url"] = url
                patched = True
            new_results.append(row)
        if patched:
            await graph.aupdate_state(config, {"task_results": new_results})
    except Exception:
        logger.warning(
            "回写 task_results.last_frame_url 失败: thread_id=%s task_id=%s",
            thread_id,
            task_id,
        )


async def _patch_task_result_status_in_graph(
    thread_id: str,
    task_id: str,
    *,
    status: str,
    video_url: str = "",
    error_message: str = "",
) -> None:
    """回调时把视频任务状态/结果 URL 同步回 graph state，供视频审阅使用。"""
    if not thread_id or not task_id:
        return
    try:
        from app.services.video_thread_service.video_graph.graph import get_graph

        graph = get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await graph.aget_state(config)
        current_results = list((snapshot.values or {}).get("task_results", []))
        patched = False
        new_results: list[dict] = []
        for tr in current_results:
            row = dict(tr)
            if row.get("task_id") == task_id:
                row["status"] = status
                if video_url:
                    row["video_url"] = video_url
                if error_message:
                    row["error_message"] = error_message
                patched = True
            new_results.append(row)
        if patched:
            await graph.aupdate_state(config, {"task_results": new_results})
    except Exception:
        logger.warning(
            "回写 task_results 视频状态失败: thread_id=%s task_id=%s",
            thread_id,
            task_id,
        )


TERMINAL_VIDEO_STATUSES = {"succeeded", "failed", "expired", "cancelled"}


def _video_results_ready(state: dict) -> bool:
    segments = state.get("script_segments") or []
    task_results = state.get("task_results") or []
    latest_by_segment: dict[int, dict] = {}
    for item in task_results:
        sid = item.get("segment_id") if isinstance(item, dict) else None
        if isinstance(sid, int):
            latest_by_segment[sid] = item
    for seg in segments:
        sid = seg.get("segment_id") if isinstance(seg, dict) else None
        if not isinstance(sid, int):
            continue
        task = latest_by_segment.get(sid)
        if not task:
            return False
        if task.get("status") not in TERMINAL_VIDEO_STATUSES:
            return False
    return bool(segments)


async def _maybe_resume_video_review(thread_id: str) -> None:
    """所有视频任务进入终态后，自动恢复 graph 到视频审阅中断点。"""
    if not thread_id:
        return
    try:
        from langgraph.errors import GraphInterrupt
        from langgraph.types import Command

        from app.services.video_thread_service.thread_lifecycle import _persist_thread_snapshot
        from app.services.video_thread_service.video_graph.graph import get_graph

        graph = get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await graph.aget_state(config)
        state = snapshot.values or {}
        if state.get("current_step") != "waiting_video_results":
            return
        if not _video_results_ready(state):
            return

        await graph.aupdate_state(config, {
            "current_step": "video_results_ready",
            "review_phase": "video",
        })
        try:
            await graph.ainvoke(
                Command(resume={"video_results_ready": True}),
                config=config,
            )
        except GraphInterrupt:
            await _persist_thread_snapshot(thread_id, workflow_ended=False)
    except Exception:
        logger.warning("自动进入视频审阅阶段失败: thread_id=%s", thread_id, exc_info=True)


async def _append_failed_chain_results(
    db: Session,
    chain_data: dict,
    remaining: list[dict],
    error_message: str,
) -> None:
    thread_id = chain_data.get("thread_id", "")
    if not remaining:
        return
    failed_results: list[dict] = []
    for seg in remaining:
        gen = Generation(
            shopify_store_id=chain_data.get("store_id", ""),
            type="video",
            status="failed",
            thread_id=thread_id or None,
            prompt_used=seg.get("description", ""),
            trend_snapshot=chain_data.get("trend"),
            brand_snapshot=chain_data.get("brand"),
            external_id=f"skipped-{int(time.time() * 1000)}-{seg.get('segment_id')}",
            error_message=error_message,
        )
        db.add(gen)
        db.commit()
        db.refresh(gen)
        failed_results.append({
            "segment_id": seg.get("segment_id"),
            "task_id": gen.external_id,
            "generation_id": gen.id,
            "status": "failed",
            "prompt": seg.get("description", ""),
            "error_message": error_message,
        })
    if thread_id:
        for result in failed_results:
            await _update_task_results_in_graph(thread_id, result)


async def continue_sequential_chain(db: Session, completed_task_id: str, continuity_image_url: str) -> None:
    from app.services.video_thread_service.video_graph.event_bus import publish_event

    redis_client = get_redis_client()
    chain_key = f"seq_chain:{completed_task_id}"
    chain_raw = await redis_client.get(chain_key)
    if not chain_raw:
        return

    chain_data = json.loads(chain_raw)
    remaining = chain_data.get("remaining_segments", [])
    thread_id = chain_data.get("thread_id", "")
    if not remaining:
        await redis_client.delete(chain_key)
        return
    await redis_client.delete(chain_key)

    next_seg = dict(remaining[0])
    if next_seg.get("mode") == "first_frame" and not continuity_image_url:
        logger.warning("串行续传: first_frame 缺少延续参考图, segment=%s", next_seg.get("segment_id"))

    try:
        from app.services.video_thread_service.video_graph.nodes import (
            build_payload_for_segment,
            create_seedance_video_task,
        )

        payload = build_payload_for_segment(
            next_seg,
            chain_data.get("config", {}),
            chain_data.get("media", {}),
            continuity_image_url=continuity_image_url,
            end_frame_url=(chain_data.get("end_frame_by_segment") or {}).get(
                str(next_seg.get("segment_id")),
                "",
            ),
        )
        submit_lf = getattr(payload, "last_frame_url", None)
        submit_lf = str(submit_lf).strip() if submit_lf else ""
        result = await create_seedance_video_task(payload)
        new_task_id = result.get("id", "")
        if not new_task_id:
            logger.error("串行续传提交未返回 task_id: segment=%s", next_seg.get("segment_id"))
            await publish_event(thread_id, "error", {
                "message": f"串行续传 segment {next_seg.get('segment_id')} 提交未返回 task_id",
            })
            await _append_failed_chain_results(
                db,
                chain_data,
                remaining,
                f"串行续传 segment {next_seg.get('segment_id')} 提交未返回 task_id",
            )
            return
    except Exception:
        logger.exception("串行续传提交失败: segment=%s", next_seg.get("segment_id"))
        await publish_event(thread_id, "error", {
            "message": f"串行续传 segment {next_seg.get('segment_id')} 提交失败",
        })
        await _append_failed_chain_results(
            db,
            chain_data,
            remaining,
            f"串行续传 segment {next_seg.get('segment_id')} 提交失败",
        )
        return

    gen = Generation(
        shopify_store_id=chain_data.get("store_id", ""),
        type="video",
        status="queued",
        thread_id=thread_id or None,
        prompt_used=next_seg.get("description", ""),
        trend_snapshot=chain_data.get("trend"),
        brand_snapshot=chain_data.get("brand"),
        external_id=new_task_id,
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    logger.info(
        "串行续传: 已提交 segment=%s, new_task_id=%s, generation_id=%s",
        next_seg.get("segment_id"),
        new_task_id,
        gen.id,
    )

    new_task_result: dict = {
        "segment_id": next_seg.get("segment_id"),
        "task_id": new_task_id,
        "generation_id": gen.id,
        "status": "queued",
        "prompt": next_seg.get("description", ""),
    }
    if submit_lf:
        new_task_result["last_frame_url"] = submit_lf

    if thread_id:
        await _update_task_results_in_graph(thread_id, new_task_result)
        await publish_event(thread_id, "segment_submitted", {
            "segment_id": next_seg.get("segment_id"),
            "task_id": new_task_id,
            "generation_id": gen.id,
            "remaining": len(remaining) - 1,
        })

    if len(remaining) > 1 and new_task_id:
        new_chain = {**chain_data, "remaining_segments": remaining[1:]}
        await redis_client.set(f"seq_chain:{new_task_id}", json.dumps(new_chain, ensure_ascii=False), ex=86400)

    try:
        await publish_generation_status(store_id=chain_data.get("store_id", ""), generation_id=gen.id, status="queued")
    except Exception:
        logger.warning("串行续传 WS 推送失败: generation_id=%s", gen.id)


async def fail_remaining_sequential_chain(db: Session, failed_task_id: str, failed_status: str) -> None:
    """串行链条中某段失败时，把后续未提交段标记为失败，避免等待节点永远挂起。"""
    from app.services.video_thread_service.video_graph.event_bus import publish_event

    redis_client = get_redis_client()
    chain_key = f"seq_chain:{failed_task_id}"
    chain_raw = await redis_client.get(chain_key)
    if not chain_raw:
        return
    chain_data = json.loads(chain_raw)
    await redis_client.delete(chain_key)

    remaining = chain_data.get("remaining_segments", [])
    thread_id = chain_data.get("thread_id", "")
    if not remaining:
        return

    await _append_failed_chain_results(
        db,
        chain_data,
        remaining,
        f"前序视频任务 {failed_status}，后续串行段未提交",
    )

    if thread_id:
        await publish_event(thread_id, "warning", {
            "message": "前序视频生成失败，后续串行段已标记为失败",
        })


async def handle_generation_status_ws(websocket: WebSocket, token: Optional[str]) -> None:
    settings = get_settings()
    heartbeat_interval = settings.WS_HEARTBEAT_INTERVAL_SECONDS
    pong_timeout = settings.WS_PONG_TIMEOUT_SECONDS

    if not token:
        await websocket.close(code=WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
        return

    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
            return
        store_id = payload.get("sub")
        if not store_id:
            await websocket.close(code=WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
            return
    except JWTError:
        await websocket.close(code=WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
        return

    await websocket.accept()
    logger.info("WebSocket 已连接: store_id=%s", store_id)

    redis_client = get_redis_client()
    pubsub = redis_client.pubsub()
    channel = f"gen:status:{store_id}"
    await pubsub.subscribe(channel)

    last_client_activity_at = time.monotonic()
    stop_event = asyncio.Event()
    session_end_reason: str | None = None

    async def _forward_redis_messages():
        try:
            async for message in pubsub.listen():
                if stop_event.is_set():
                    break
                if message["type"] == "message":
                    await websocket.send_text(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WS forward 任务异常: store_id=%s", store_id)
        finally:
            stop_event.set()

    async def _heartbeat():
        try:
            while not stop_event.is_set():
                try:
                    await websocket.send_json({"event": "ping"})
                except Exception:
                    break
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_interval)
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            stop_event.set()

    async def _receive_client_messages():
        nonlocal last_client_activity_at, session_end_reason
        try:
            while not stop_event.is_set():
                data = await websocket.receive_text()
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    session_end_reason = "invalid_client_message"
                    break
                if not isinstance(payload, dict):
                    session_end_reason = "invalid_client_message"
                    break

                event = payload.get("event")
                if not isinstance(event, str) or not event:
                    session_end_reason = "invalid_client_message"
                    break
                if event == "close":
                    session_end_reason = "client_closed"
                    break
                last_client_activity_at = time.monotonic()
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WS receive 任务异常: store_id=%s", store_id)
        finally:
            stop_event.set()

    async def _client_activity_timeout_watcher():
        nonlocal session_end_reason
        try:
            check_interval = max(1, min(heartbeat_interval, max(1, pong_timeout // 2)))
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=check_interval)
                    break
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() - last_client_activity_at > pong_timeout:
                    session_end_reason = "client_activity_timeout"
                    break
        except asyncio.CancelledError:
            raise
        finally:
            stop_event.set()

    tasks = [
        asyncio.create_task(_forward_redis_messages()),
        asyncio.create_task(_heartbeat()),
        asyncio.create_task(_receive_client_messages()),
        asyncio.create_task(_client_activity_timeout_watcher()),
    ]

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            logger.warning("Redis unsubscribe 失败: channel=%s", channel, exc_info=True)
        try:
            await pubsub.aclose()
        except Exception:
            logger.warning("Redis pubsub close 失败: channel=%s", channel, exc_info=True)

        if session_end_reason == "client_activity_timeout":
            close_code, close_reason = WS_CLOSE_CLIENT_ACTIVITY_TIMEOUT, "client_activity_timeout"
        elif session_end_reason == "client_closed":
            close_code, close_reason = WS_CLOSE_NORMAL, "client_closed"
        elif session_end_reason == "invalid_client_message":
            close_code, close_reason = WS_CLOSE_INVALID_CLIENT_JSON, "invalid_client_message"
        else:
            close_code, close_reason = WS_CLOSE_NORMAL, "server_closed"

        try:
            await websocket.close(code=close_code, reason=close_reason)
        except Exception:
            pass

        logger.info("WebSocket 已断开: store_id=%s, code=%s, reason=%s", store_id, close_code, close_reason)


# async def query_video_task_status(task_id: str) -> VideoTaskStatusResponse:
#     try:
#         result = await query_video_task(task_id)
#     except ValueError as e:
#         raise HTTPException(status_code=503, detail=str(e))
#     except HTTPStatusError as e:
#         logger.error("Seedance query task upstream error: %s", e.response.text)
#         raise HTTPException(status_code=e.response.status_code, detail=f"上游 API 错误: {e.response.text}")
#     except Exception as e:
#         logger.exception("Seedance query task unexpected error")
#         raise HTTPException(status_code=500, detail=str(e))
#
#     return VideoTaskStatusResponse(
#         id=result.get("id"),
#         model=result.get("model"),
#         status=result.get("status", "unknown"),
#         created_at=result.get("created_at"),
#         updated_at=result.get("updated_at"),
#         content=result.get("content"),
#         usage=result.get("usage"),
#         error=result.get("error"),
#     )

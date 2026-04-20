import asyncio
import json
import logging
import time
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from httpx import HTTPStatusError
from jose import JWTError
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.config import get_settings
from app.core.responses import success
from app.core.security import decode_access_token
from app.db.mysql import get_db
from app.db.redis import get_redis_client
from app.models import Merchant, Generation
from app.schemas.content import (
    GenerationOut,
    Text2VideoRequest,
    Image2VideoRequest,
    Ref2VideoRequest,
    CreateVideoTaskResponse,
    VideoTaskStatusResponse,
    VideoTaskCallbackRequest,
    TrendProductVideoRequest,
)
from app.services.content_service import (
    get_generation_by_id,
    handle_video_task_callback,
    refresh_video_status,
)
from app.services.content_service.trend_video_service import build_seedance_payload
from app.services.notification_service import publish_generation_status
from app.services.seedance_client import (
    create_seedance_video_task,
    query_seedance_video_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generate", tags=["generate"])

#


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
    task_id = payload.id
    status = payload.status
    logger.info("收到方舟回调: task_id=%s, status=%s", task_id, status)

    video_url = None
    if payload.content and payload.content.video_url:
        video_url = payload.content.video_url

    error_msg = None
    if payload.error and payload.error.message:
        error_msg = payload.error.message

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
        return success(data={"received": True, "matched": False})

    await publish_generation_status(
        store_id=gen.shopify_store_id,
        generation_id=gen.id,
        status=gen.status,
        video_url=gen.result_url,
        error_message=gen.error_message,
    )

    if status == "succeeded":
        last_frame_url = ""
        if payload.content and payload.content.last_frame_url:
            last_frame_url = payload.content.last_frame_url
        await _continue_sequential_chain(db, task_id, last_frame_url)

    logger.info(
        "回调处理完成: task_id=%s, generation_id=%s, status=%s",
        task_id, gen.id, gen.status,
    )
    return success(data={"received": True, "matched": True, "generation_id": gen.id})


async def _continue_sequential_chain(
    db: Session,
    completed_task_id: str,
    last_frame_url: str,
) -> None:
    """
    串行链条续传：当一段视频生成成功后，检查 Redis 是否还有待提交的后续段。
    有则取出下一段，注入上一段的尾帧作为首帧，提交并创建 Generation 记录。
    """
    from app.db.redis import get_redis_client
    from app.services.video_graph.payload_builder import build_payload_for_segment

    redis_client = get_redis_client()
    chain_key = f"seq_chain:{completed_task_id}"
    chain_raw = await redis_client.get(chain_key)
    if not chain_raw:
        return

    import json as _json
    chain_data = _json.loads(chain_raw)
    remaining = chain_data.get("remaining_segments", [])
    if not remaining:
        await redis_client.delete(chain_key)
        return

    await redis_client.delete(chain_key)

    next_seg = dict(remaining[0])
    if next_seg.get("mode") == "frame_interpolation" and last_frame_url:
        next_seg["first_frame_url"] = last_frame_url
    elif next_seg.get("mode") == "frame_interpolation" and not next_seg.get("first_frame_url"):
        logger.warning(
            "串行续传: frame_interpolation 缺少 first_frame_url, segment=%s",
            next_seg.get("segment_id"),
        )

    config = chain_data.get("config", {})
    store_id = chain_data.get("store_id", "")

    try:
        payload = build_payload_for_segment(next_seg, config)
        result = await create_seedance_video_task(payload)
        new_task_id = result.get("id", "")
    except Exception:
        logger.exception("串行续传提交失败: segment=%s", next_seg.get("segment_id"))
        return

    gen = Generation(
        shopify_store_id=store_id,
        type="video",
        status="queued",
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
        next_seg.get("segment_id"), new_task_id, gen.id,
    )

    if len(remaining) > 1 and new_task_id:
        new_chain = {**chain_data, "remaining_segments": remaining[1:]}
        await redis_client.set(
            f"seq_chain:{new_task_id}",
            _json.dumps(new_chain, ensure_ascii=False),
            ex=86400,
        )

    try:
        await publish_generation_status(
            store_id=store_id,
            generation_id=gen.id,
            status="queued",
        )
    except Exception:
        logger.warning("串行续传 WS 推送失败: generation_id=%s", gen.id)


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
# WebSocket 关闭状态码（应用层自定义，1000~2999 由协议保留）
_WS_CLOSE_NORMAL = 1000           # 正常关闭（客户端主动 close）
_WS_CLOSE_CLIENT_ACTIVITY_TIMEOUT = 4002  # 长时间无合法客户端消息，判定离线
_WS_CLOSE_INVALID_TOKEN = 4001    # JWT 鉴权失败
_WS_CLOSE_INVALID_CLIENT_JSON = 1008  # RFC：违反策略；此处表示非约定 JSON / 缺少 event


@router.websocket("/ws/status")
async def ws_generation_status(
    websocket: WebSocket,
    token: str = Query(...),
):
    settings = get_settings()
    heartbeat_interval = settings.WS_HEARTBEAT_INTERVAL_SECONDS
    pong_timeout = settings.WS_PONG_TIMEOUT_SECONDS

    # ---- 1. JWT 鉴权 ----
    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=_WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
            return
        store_id = payload.get("sub")
        if not store_id:
            await websocket.close(code=_WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
            return
    except JWTError:
        await websocket.close(code=_WS_CLOSE_INVALID_TOKEN, reason="invalid_token")
        return

    await websocket.accept()
    logger.info("WebSocket 已连接: store_id=%s", store_id)

    # ---- 2. 订阅该商户的 Redis channel ----
    redis_client = get_redis_client()
    pubsub = redis_client.pubsub()
    channel = f"gen:status:{store_id}"
    await pubsub.subscribe(channel)

    # 最近一次「合法客户端 JSON 消息」刷新的单调时钟时间（含 pong 等任意 event）
    last_client_activity_at = time.monotonic()
    # 任一分支决定结束时置位，其他分支感知后退出
    stop_event = asyncio.Event()
    # 统一会话结束原因，提升可读性并集中映射 close code
    session_end_reason: str | None = None

    async def _forward_redis_messages():
        """从 Redis Pub/Sub 读取消息并转发到 WebSocket。"""
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
        """连接建立后先发一次 ping，再按固定周期发送。"""
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
        """
        监听客户端消息：
        - {"event":"pong"}  → 更新 last_pong_at
        - {"event":"close"} → 客户端主动断开，标记并结束
        """
        nonlocal last_client_activity_at, session_end_reason
        try:
            while not stop_event.is_set():
                data = await websocket.receive_text()
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("WS 收到非 JSON 文本，断开: store_id=%s", store_id)
                    session_end_reason = "invalid_client_message"
                    break

                if not isinstance(payload, dict):
                    logger.warning("WS JSON 须为对象，断开: store_id=%s", store_id)
                    session_end_reason = "invalid_client_message"
                    break

                event = payload.get("event")
                if not isinstance(event, str) or not event:
                    logger.warning("WS JSON 缺少合法 event 字段，断开: store_id=%s", store_id)
                    session_end_reason = "invalid_client_message"
                    break

                if event == "close":
                    session_end_reason = "client_closed"
                    logger.info("WS 客户端主动断开: store_id=%s", store_id)
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
        """超过 pong_timeout 未收到任何合法客户端消息则判离线。"""
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
                    logger.info(
                        "WS 客户端活跃超时，断开: store_id=%s, timeout=%ss",
                        store_id, pong_timeout,
                    )
                    break
        except asyncio.CancelledError:
            raise
        finally:
            stop_event.set()

    # ---- 3. 并发运行：Redis 转发 + 心跳 + 客户端监听 + 超时监控 ----
    forward_task = asyncio.create_task(_forward_redis_messages())
    heartbeat_task = asyncio.create_task(_heartbeat())
    receive_task = asyncio.create_task(_receive_client_messages())
    watcher_task = asyncio.create_task(_client_activity_timeout_watcher())
    tasks = [forward_task, heartbeat_task, receive_task, watcher_task]

    try:
        await stop_event.wait()
    finally:
        # ---- 4. 清理：取消后台任务、退订 Redis、关闭 WS ----
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
            close_code, close_reason = _WS_CLOSE_CLIENT_ACTIVITY_TIMEOUT, "client_activity_timeout"
        elif session_end_reason == "client_closed":
            close_code, close_reason = _WS_CLOSE_NORMAL, "client_closed"
        elif session_end_reason == "invalid_client_message":
            close_code, close_reason = _WS_CLOSE_INVALID_CLIENT_JSON, "invalid_client_message"
        else:
            close_code, close_reason = _WS_CLOSE_NORMAL, "server_closed"

        try:
            await websocket.close(code=close_code, reason=close_reason)
        except Exception:
            pass

        logger.info(
            "WebSocket 已断开: store_id=%s, code=%s, reason=%s",
            store_id, close_code, close_reason,
        )


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
    return success(GenerationOut.model_validate(gen))



# # ----------------------------------------------------------
# # 视频生成 — Seedance 1.5 Pro (火山引擎方舟)
# # 文生视频 (text2video): content 只含 text 项
# # 图生视频 (image2video): content 含 text + image_url 项
# # ----------------------------------------------------------
# @router.post("/generate-video", response_model=dict)
# async def generate_video(
#     payload: Union[Image2VideoRequest, Text2VideoRequest],
#     current_merchant: Merchant = Depends(get_current_merchant),
# ):
#     """
#     调用 Seedance 1.5 Pro 创建视频生成任务。
#
#     - **文生视频**: content 仅包含 type=text 的项
#     - **图生视频**: content 同时包含 type=text 和 type=image_url 的项
#
#     请求/响应格式与火山引擎官方 API 一致。成功后返回任务 ID，
#     可调用 GET /generate/video-task-status/{task_id} 轮询任务状态。
#     """
#     try:
#         request_body = payload.model_dump(exclude_none=True)
#         result = await create_seedance_video_task(request_body)
#     except ValueError as e:
#         raise HTTPException(status_code=503, detail=str(e))
#     except HTTPStatusError as e:
#         logger.error("Seedance create task upstream error: %s", e.response.text)
#         raise HTTPException(
#             status_code=e.response.status_code,
#             detail=f"上游 API 错误: {e.response.text}",
#         )
#     except Exception as e:
#         logger.exception("Seedance create task unexpected error")
#         raise HTTPException(status_code=500, detail=str(e))
#
#     return success(
#         CreateVideoTaskResponse(
#             id=result.get("id", ""),
#             status=result.get("status", "submitted"),
#         )
#     )
#
#
# # ----------------------------------------------------------
# # 参考图生视频 — Seedance 1.0 Lite i2v
# # content 含 1~4 张参考图(role=reference_image) + 可选文本
# # ----------------------------------------------------------
# @router.post("/ref2video", response_model=dict)
# async def generate_ref2video(
#     payload: Ref2VideoRequest,
#     current_merchant: Merchant = Depends(get_current_merchant),
# ):
#     """
#     调用 Seedance 1.0 Lite i2v 创建参考图生视频任务。
#
#     - content 包含 **1~4 张参考图**（type=image_url, role=reference_image）
#     - 可选文本提示词，推荐使用 "[图1]xxx，[图2]xxx" 格式指定图片组合
#     - 参考图场景不支持 1080p 分辨率、不支持 adaptive 宽高比
#
#     成功后返回任务 ID，可调用 GET /generate/video-task-status/{task_id} 轮询状态。
#     """
#     try:
#         request_body = payload.model_dump(exclude_none=True)
#         result = await create_seedance_video_task(request_body)
#     except ValueError as e:
#         raise HTTPException(status_code=503, detail=str(e))
#     except HTTPStatusError as e:
#         logger.error("Seedance Lite i2v create task upstream error: %s", e.response.text)
#         raise HTTPException(
#             status_code=e.response.status_code,
#             detail=f"上游 API 错误: {e.response.text}",
#         )
#     except Exception as e:
#         logger.exception("Seedance Lite i2v create task unexpected error")
#         raise HTTPException(status_code=500, detail=str(e))
#
#     return success(
#         CreateVideoTaskResponse(
#             id=result.get("id", ""),
#             status=result.get("status", "submitted"),
#         )
#     )
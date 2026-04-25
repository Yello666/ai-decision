"""
Seedance 2.0 视频生成 API 路由。

端点:
  POST /generations/seedance2/video           创建视频生成任务
  GET  /generations/seedance2/video/{task_id}  查询任务状态
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.core.responses import success
from app.schemas.seedance2 import Seedance2VideoRequest
from app.services.seedance2_service import (
    create_video_task,
    query_video_task,
    parse_task_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generations/seedance2", tags=["seedance2"])


def _upstream_error_detail(exc: httpx.HTTPStatusError) -> Any:
    """提取上游官方错误响应，优先保留 JSON 结构。"""
    try:
        return exc.response.json()
    except Exception:
        return exc.response.text


# ----------------------------------------------------------
# POST /generations/seedance2/video
# 创建 Seedance 2.0 视频生成任务（异步任务，立即返回 task_id）
# ----------------------------------------------------------
@router.post("/video", response_model=dict)
async def generate_seedance2_video(payload: Seedance2VideoRequest):
    """
    创建 Seedance 2.0 视频生成任务。

    **生成模式（互斥）:**
    - `text_to_video`:        纯文生视频，仅需提示词
    - `first_frame`:          首帧图生视频，需提供 first_frame_url
    - `first_last_frame`:     首尾帧图生视频，需提供 first_frame_url + last_frame_url
    - `multimodal_reference`: 多模态参考生视频，支持 0~9 图 + 0~3 视频 + 0~3 音频

    **异步任务:** 提交后返回 task_id，使用
    `GET /generations/seedance2/video/{task_id}` 轮询状态。

    **提示词格式（多模态参考模式）:** 使用 [图1][图2][图3][音频1][视频1] 格式
    在提示词内明确说明每个素材的用途、动作、画面逻辑。
    """
    try:
        result = await create_video_task(payload)
        return success(parse_task_response(result))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        detail = _upstream_error_detail(exc)
        logger.error(
            "Seedance 2.0 上游创建任务失败: status=%s body=%s",
            exc.response.status_code,
            detail,
        )
        raise HTTPException(status_code=exc.response.status_code, detail=detail)
    except Exception:
        logger.exception("Seedance 2.0 视频生成任务创建失败")
        raise HTTPException(
            status_code=502,
            detail="Seedance 2.0 服务异常，请检查 API Key / Model ID 配置",
        )


# ----------------------------------------------------------
# GET /generations/seedance2/video/{task_id}
# 查询任务状态（轮询直到终态）
# ----------------------------------------------------------
@router.get("/video/{task_id}", response_model=dict)
async def get_seedance2_video_task(task_id: str):
    """
    查询 Seedance 2.0 视频生成任务状态。

    **状态流转:** submitted → queued → running → succeeded / failed / expired

    当 `status=succeeded` 时，`video_url` 包含生成的视频地址（24h 有效，请及时转存）。
    当 `status=failed` 时，`error` 包含失败原因。
    """
    try:
        result = await query_video_task(task_id)
        return success(parse_task_response(result))
    except httpx.HTTPStatusError as exc:
        detail = _upstream_error_detail(exc)
        logger.error(
            "Seedance 2.0 上游查询任务失败: status=%s body=%s task_id=%s",
            exc.response.status_code,
            detail,
            task_id,
        )
        raise HTTPException(status_code=exc.response.status_code, detail=detail)
    except Exception:
        logger.exception(
            "查询 Seedance 2.0 任务失败: task_id=%s", task_id,
        )
        raise HTTPException(
            status_code=502,
            detail=f"查询任务 {task_id} 失败，请稍后重试",
        )

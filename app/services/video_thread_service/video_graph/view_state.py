"""
FrontendViewState —— 前端视图态 DTO + Mapper

核心思想（状态隔离）：
  - VideoGenerationState 是 LangGraph 的运行态，内含 raw_prompt、parsed_media、
    shopify_store_id 等后端内部字段，严禁直接外泄给前端。
  - FrontendViewState 是前端的表现态，粒度小、语义清晰，能够直接驱动 UI。

使用方式：
    view = map_graph_state_to_view(snapshot.values)

Mapper 保证：
  - status 由 current_step 确定性映射（running / waiting_human / finished / error）
  - progress 给出固定的里程碑百分比，前端可据此直接渲染进度条
  - message 是"已做人话翻译"的文案，业务层无需再写文案
  - segments 在 waiting_human / finished 下发；在 waiting_video_results / video_results_ready
    也会下发（只读），便于晚进入页面的用户仍能看到分镜清单与各段生成状态
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 前端视图态 DTO
# ──────────────────────────────────────────────
FrontendStatus = Literal["running", "waiting_human", "finished", "error"]


class FrontendViewState(BaseModel):
    """开箱即用的前端视图态。所有字段都可以直接绑定 UI 组件。"""

    status: FrontendStatus = Field(
        description="页面核心骨架决定项：running=进度条页 / waiting_human=交互弹窗 / finished=结果页 / error=错误页",
    )
    message: str = Field(description="用户友好提示语，可直接显示")
    progress: int = Field(ge=0, le=100, description="进度百分比 0~100")

    # --- 业务数据：按需下发 ---
    segments: Optional[list[dict]] = Field(
        default=None,
        description="剧本分镜草稿；waiting_human / finished 下发；waiting_video_results 等也会只读下发",
    )
    total_duration: Optional[int] = None
    execution_strategy: Optional[Literal["parallel", "sequential"]] = None
    revision_count: Optional[int] = None

    # --- 任务结果（审视频 / 等结果 / finished）---
    task_results: Optional[list[dict]] = None
    video_results: Optional[list[dict]] = None
    final_status: Optional[str] = None
    review_phase: Optional[str] = None
    target_segment_ids: Optional[list[int]] = None

    # --- 错误详情（error）---
    error_detail: Optional[str] = None

    # --- 便于前端乐观拉取 ---
    current_step: Optional[str] = Field(
        default=None, description="后端步骤标识，仅用于调试/日志，非 UI 决策字段"
    )


# ──────────────────────────────────────────────
# current_step → (status, progress, message) 映射表
# ──────────────────────────────────────────────
# progress 采用"里程碑式"设计：每个节点落地后给一个确定值
# 其他细粒度的百分比由 SSE progress 事件实时微调
_STEP_META: dict[str, tuple[FrontendStatus, int, str]] = {
    # 初始化
    "initializing":                ("running",        2,   "任务初始化中…"),
    # 节点 A
    "parse_intent_running":        ("running",        8,   "正在识别需求与参数…"),
    "parse_intent_done":           ("running",        20,  "参数解析完成，开始构思分镜…"),
    # 节点 B
    "plan_script_running":         ("running",        35,  "AI 正在构思视频分镜…"),
    "plan_script_done":            ("running",        55,  "分镜草稿生成完毕，等待您审阅"),
    # 节点 C（人机交互）
    "waiting_human":               ("waiting_human",  55,  "请审阅剧本草稿并确认 / 修改 / 反馈"),
    "human_responded":             ("running",        60,  "已收到您的反馈，正在处理…"),
    # 编辑 / 重写
    "apply_edit_running":          ("running",        62,  "正在应用您的编辑…"),
    "revise_script_running":       ("running",        70,  "AI 正在根据您的反馈重写剧本…"),
    # 节点 D / E
    "assemble_and_submit_running": ("running",        85,  "正在向视频生成引擎提交任务…"),
    "submitted":                   ("running",        95,  "任务已提交，正在登记生成记录…"),
    "done":                        ("finished",       100, "视频生成任务已成功提交"),
    # 视频结果审阅
    "waiting_video_results":       ("running",        98,  "视频生成中，请稍候…"),
    "video_results_ready":         ("running",        99,  "视频结果已生成，准备进入审阅…"),
    "waiting_human_vd":             ("waiting_human",  100, "请审阅视频生成结果"),
    "video_human_responded":        ("running",        100, "已收到您的视频审阅反馈，正在处理…"),
    "finished":                    ("finished",       100, "视频结果已确认"),
    # 终态错误
    "error":                       ("error",          100, "任务执行失败"),
}


# ──────────────────────────────────────────────
# Segments 脱敏 / 语言化
# ──────────────────────────────────────────────
def format_segments_for_view(segments: list[dict], language: str) -> list[dict]:
    """
    移除后端内部字段（如 image_urls 内部路径等），给前端一个干净的视图。
    保留 description / description_en / description_zh 双语字段，便于 UI 切换。
    """
    result: list[dict] = []
    for seg in segments or []:
        desc_en = seg.get("description") or ""
        desc_zh = seg.get("description_zh") or ""
        primary = desc_zh if language == "zh" and desc_zh else desc_en
        result.append({
            "segment_id": seg.get("segment_id"),
            "description": primary,
            "description_en": desc_en,
            "description_zh": desc_zh,
            "duration": seg.get("duration"),
            "mode": seg.get("mode"),
        })
    return result


# ──────────────────────────────────────────────
# 核心 Mapper
# ──────────────────────────────────────────────
def map_graph_state_to_view(
    graph_state: Optional[dict],
    *,
    fallback_step: str = "initializing",
) -> FrontendViewState:
    """
    将 VideoGenerationState（TypedDict / dict）转换为 FrontendViewState。

    当 graph_state 为空（例如刚创建尚未进入第一个节点）时，返回 initializing 视图。
    """
    state = graph_state or {}

    # --- 1) 错误态优先 ---
    error_msg = state.get("error")
    if error_msg or state.get("current_step") == "error":
        _, progress, message = _STEP_META["error"]
        return FrontendViewState(
            status="error",
            message=message,
            progress=progress,
            error_detail=error_msg or "未知错误",
            current_step="error",
        )

    # --- 2) 正常步骤映射 ---
    step = state.get("current_step") or fallback_step
    status, progress, message = _STEP_META.get(
        step, ("running", 1, "任务处理中…")
    )

    config = state.get("parsed_config") or {}
    language = config.get("language", "zh")
    raw_segments = state.get("script_segments") or []
    task_results = state.get("task_results", [])

    view = FrontendViewState(
        status=status,
        message=message,
        progress=progress,
        current_step=step,
        revision_count=state.get("revision_count"),
        review_phase=state.get("review_phase"),
        target_segment_ids=state.get("target_segment_ids"),
    )

    # --- 3) 不同阶段按需下发业务数据 ---
    if status == "waiting_human":
        view.segments = format_segments_for_view(raw_segments, language)
        view.total_duration = (
            state["total_duration"] if "total_duration" in state else 0
        )
        view.execution_strategy = state.get("execution_strategy", "parallel")
        if step == "waiting_human_vd":
            view.task_results = task_results
            view.video_results = _format_video_results_for_view(raw_segments, task_results)
    elif step in ("waiting_video_results", "video_results_ready"):
        # running 态但需展示分镜与各段任务进度（含晚进入会话、无本地 segment 缓存时）
        view.segments = format_segments_for_view(raw_segments, language)
        view.total_duration = (
            state["total_duration"] if "total_duration" in state else 0
        )
        view.execution_strategy = state.get("execution_strategy", "parallel")
        view.task_results = task_results
        view.video_results = _format_video_results_for_view(raw_segments, task_results)
    elif status == "finished":
        view.segments = format_segments_for_view(raw_segments, language)
        view.total_duration = (
            state["total_duration"] if "total_duration" in state else 0
        )
        view.execution_strategy = state.get("execution_strategy", "parallel")
        view.task_results = task_results
        view.video_results = _format_video_results_for_view(raw_segments, task_results)
        view.final_status = state.get("final_status", "submitted")

    return view


def _format_video_results_for_view(
    segments: list[dict],
    task_results: list[dict],
) -> list[dict]:
    """按 segment 汇总视频任务结果，供视频审阅 UI 直接渲染。"""
    result_by_segment = {
        item.get("segment_id"): item
        for item in task_results or []
        if item.get("segment_id") is not None
    }
    rows: list[dict] = []
    for seg in segments or []:
        sid = seg.get("segment_id")
        task = result_by_segment.get(sid, {})
        rows.append({
            "segment_id": sid,
            "task_id": task.get("task_id"),
            "generation_id": task.get("generation_id"),
            "status": task.get("status"),
            "video_url": task.get("video_url"),
            "error_message": task.get("error_message"),
            "last_frame_url": task.get("last_frame_url"),
        })
    return rows


__all__ = [
    "FrontendViewState",
    "FrontendStatus",
    "format_segments_for_view",
    "map_graph_state_to_view",
]

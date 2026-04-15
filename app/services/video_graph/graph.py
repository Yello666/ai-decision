"""
LangGraph 视频生成编排系统 —— Graph 定义与组装。

工作流:
  parse_intent → plan_script → present_to_human → [interrupt]
    ↳ approve  → assemble_and_submit → respond → END
    ↳ edit     → apply_edit → present_to_human → [interrupt]
    ↳ feedback → revise_script → present_to_human → [interrupt]
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.services.video_graph.state import VideoGenerationState
from app.services.video_graph.nodes import (
    apply_edit,
    assemble_and_submit,
    parse_intent,
    plan_script,
    present_to_human,
    respond,
    revise_script,
    route_human_action,
)

logger = logging.getLogger(__name__)


def _human_interrupt_node(state: VideoGenerationState) -> dict:
    """
    真正的 human-in-the-loop 中断点。
    LangGraph 在此暂停执行，将控制权交给前端。
    前端通过 resume 接口注入 human_action 等字段后继续。
    """
    segments = state.get("script_segments", [])
    config = state.get("parsed_config", {})
    lang = config.get("language", "zh")

    display_segments = []
    for seg in segments:
        display_segments.append({
            "segment_id": seg.get("segment_id"),
            "description": seg.get("description_zh") if lang == "zh" else seg.get("description"),
            "description_en": seg.get("description"),
            "duration": seg.get("duration"),
            "mode": seg.get("mode"),
        })

    human_input = interrupt({
        "type": "need_human_input",
        "segments": display_segments,
        "total_duration": state.get("total_duration", 0),
        "execution_strategy": state.get("execution_strategy", "parallel"),
        "revision_count": state.get("revision_count", 0),
        "message": "请审阅剧本，选择: approve(确认生成) / edit(修改后生成) / feedback(提出意见重新生成)",
    })

    return {
        "human_action": human_input.get("action", "approve"),
        "human_edited_segments": human_input.get("edited_segments", []),
        "human_feedback": human_input.get("feedback", ""),
        "current_step": "human_responded",
    }


def _route_after_error(state: VideoGenerationState) -> str:
    """检查是否有错误，有则终止。"""
    if state.get("error"):
        return END
    return "continue"


def build_video_graph() -> StateGraph:
    """构建并编译视频生成 LangGraph。"""

    graph = StateGraph(VideoGenerationState)

    # ── 注册节点 ──
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("plan_script", plan_script)
    graph.add_node("present_to_human", present_to_human)
    graph.add_node("human_interrupt", _human_interrupt_node)
    graph.add_node("apply_edit", apply_edit)
    graph.add_node("revise_script", revise_script)
    graph.add_node("assemble_and_submit", assemble_and_submit)
    graph.add_node("respond", respond)

    # ── 入口 ──
    graph.set_entry_point("parse_intent")

    # ── 边: parse_intent → plan_script (带错误检查) ──
    graph.add_conditional_edges(
        "parse_intent",
        lambda s: END if s.get("current_step") == "error" else "plan_script",
    )

    # ── 边: plan_script → present_to_human (带错误检查) ──
    graph.add_conditional_edges(
        "plan_script",
        lambda s: END if s.get("current_step") == "error" else "present_to_human",
    )

    # ── 边: present_to_human → human_interrupt ──
    graph.add_edge("present_to_human", "human_interrupt")

    # ── 边: human_interrupt → 条件路由 ──
    graph.add_conditional_edges(
        "human_interrupt",
        route_human_action,
        {
            "assemble_and_submit": "assemble_and_submit",
            "apply_edit": "apply_edit",
            "revise_script": "revise_script",
            "present_to_human": "present_to_human",
        },
    )

    # ── 边: apply_edit → present_to_human（编辑后再次确认）──
    graph.add_edge("apply_edit", "present_to_human")

    # ── 边: revise_script → present_to_human (带错误检查) ──
    graph.add_conditional_edges(
        "revise_script",
        lambda s: END if s.get("current_step") == "error" else "present_to_human",
    )

    # ── 边: assemble_and_submit → respond ──
    graph.add_edge("assemble_and_submit", "respond")

    # ── 边: respond → END ──
    graph.add_edge("respond", END)

    return graph


@lru_cache(maxsize=1)
def get_graph():
    """获取编译后的 Graph 单例（带 MemorySaver checkpointer）。"""
    graph = build_video_graph()
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)

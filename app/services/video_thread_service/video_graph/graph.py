"""
LangGraph 视频生成编排系统 —— Graph 定义与组装。

工作流:
  parse_intent → plan_script → set_waiting_human → [interrupt]
    ↳ approve  → assemble_and_submit → respond → END
    ↳ edit     → apply_edit → set_waiting_human → [interrupt]
    ↳ feedback → revise_script → set_waiting_human → [interrupt]
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.graph import END, StateGraph


from app.db.postgres import get_checkpointer
from app.services.video_thread_service.video_graph.state import VideoGenerationState
from app.services.video_thread_service.video_graph.nodes import (
    apply_edit,
    assemble_and_submit,
    parse_intent,
    plan_script,
    set_waiting_human,
    respond,
    revise_script,
    route_human_action, human_interrupt,
)

logger = logging.getLogger(__name__)


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
    graph.add_node("set_waiting_human", set_waiting_human)
    graph.add_node("human_interrupt", human_interrupt)
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
    # 语法解析：
    # 第 2 个参数是一个匿名路由函数，LangGraph 会调用这个函数，并把当前 state 传进去，让它返回“下一跳”
    # s是传入的参数 state，即当前路由状态。匿名函数lambda s(入参): return的值
    #整条语句相当于下面：
    # def route_fn(s):
    #     return END if s.get("current_step") == "error" else "plan_script"
    # graph.add_conditional_edges("parse_intent", route_fn)

    # ── 边: plan_script → set_waiting_human (带错误检查) ──
    graph.add_conditional_edges(
        "plan_script",
        lambda s: END if s.get("current_step") == "error" else "set_waiting_human",
    )

    # ── 边: set_waiting_human → human_interrupt ──
    graph.add_edge("set_waiting_human", "human_interrupt")


    # ── 边: human_interrupt → 条件路由 ──
    graph.add_conditional_edges(
        "human_interrupt",
        route_human_action,
        {
            "assemble_and_submit": "assemble_and_submit",
            "apply_edit": "apply_edit",
            "revise_script": "revise_script",
            "set_waiting_human": "set_waiting_human",
        },
    )

    # ── 边: apply_edit → set_waiting_human（编辑后再次确认）──
    graph.add_edge("apply_edit", "set_waiting_human")

    # ── 边: revise_script → set_waiting_human (带错误检查) ──
    graph.add_conditional_edges(
        "revise_script",
        lambda s: END if s.get("current_step") == "error" else "set_waiting_human",
    )

    # ── 边: assemble_and_submit → respond ──
    graph.add_edge("assemble_and_submit", "respond")

    # ── 边: respond → END ──
    graph.add_edge("respond", END)

    return graph



@lru_cache(maxsize=1)
def get_graph():
    """获取编译后的 Graph 单例（带 Postgres checkpointer）。

    注意：首次调用必须发生在 FastAPI lifespan 完成 Postgres 初始化之后，
    否则 ``get_checkpointer()`` 会抛错。
    """
    graph = build_video_graph()
    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)

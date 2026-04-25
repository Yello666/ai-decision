"""
LangGraph 视频生成编排系统 —— 全局状态定义。

VideoGenerationState 是 Graph 中所有节点共享的状态对象。
每次节点执行后，LangGraph 会自动将返回的 dict merge 到当前 state。
"""
from __future__ import annotations

from typing import Literal, Optional
from typing_extensions import TypedDict


class ScriptSegment(TypedDict, total=False):
    """拆分后的单个剧情片段。"""
    segment_id: int
    description: str
    description_zh: str
    duration: int
    mode: Literal["text_to_video", "multimodal_reference", "first_frame"]
    image_urls: list[str]
    first_frame_url: str
    last_frame_url: str
    reference_image_urls: list[str]
    reference_video_urls: list[str]
    reference_audio_urls: list[str]


class ConfigParams(TypedDict, total=False):
    resolution: Literal["480p", "720p"]
    ratio: str
    language: Literal["zh", "en"]
    watermark: bool
    generate_audio: bool


# 默认参数：用户未显式指定时的兜底值。
# 放在 state.py 便于 service / nodes 共享，避免反向 import。
DEFAULT_CONFIG: ConfigParams = {
    "resolution": "720p",
    "ratio": "adaptive",
    "language": "zh",
    "watermark": False,
    "generate_audio": True,
}


class TaskResult(TypedDict, total=False):
    """单个 Seedance 任务的提交结果。"""
    segment_id: int
    task_id: str
    generation_id: int
    status: str
    prompt: str


class VideoGenerationState(TypedDict, total=False):
    """Graph 全局状态，贯穿整个视频生成工作流。

    职责：作为"后端运行态"，保留所有节点需要的上下文。
    不直接作为 API 响应返回给前端，
    经 `app.services.video_graph.view_state.map_graph_state_to_view` 转换。
    """

    # ---- 会话标识（用于 SSE 事件总线路由） ----
    thread_id: str

    # ---- 输入（创建 thread 时由 API 层写入） ----
    user_input: str
    trend: dict
    brand: dict
    product: dict
    generation_mode: Literal["text_to_video", "multimodal_reference"]
    media_assets: dict
    config_params: ConfigParams
    shopify_store_id: str

    # ---- 节点 A 提取结果 ----
    parsed_mode: Literal["text_to_video", "multimodal_reference"]
    parsed_config: ConfigParams
    parsed_media: dict
    audio_prompt_fixed: bool

    # ---- 节点 B 生成结果 ----
    raw_prompt: str
    optimized_prompt: str
    script_segments: list[ScriptSegment]
    total_duration: int
    execution_strategy: Literal["parallel", "sequential"]

    # ---- 节点 C 人机交互 ----
    human_action: Literal["approve", "edit", "feedback"] | None
    human_edited_segments: list[ScriptSegment]
    human_feedback: str
    revision_count: int

    # ---- 节点 D/E 执行结果 ----
    task_results: list[TaskResult]
    final_status: str

    # ---- 流程控制 ----
    current_step: str  #流程的业务状态，不是运行到的节点名称
    error: str
    # current_step有以下的值：
    # initializing: 初始化
    # parse_intent_running: 解析意图
    # parse_intent_done: 解析意图完成
    # plan_script_running: 生成剧本
    # plan_script_done: 生成剧本完成
    # waiting_human: 等待人类输入
    # human_responded: 人类输入完成
    # apply_edit_running: 应用编辑
    # apply_edit_done: 应用编辑完成
    # revise_script_running: 重新生成剧本
    # revise_script_done: 重新生成剧本完成
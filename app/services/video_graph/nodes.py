"""
LangGraph 视频生成编排系统 —— 节点实现。

节点 A: parse_intent        意图识别与参数提取
节点 B: plan_script         剧情规划与 Prompt 工程
节点 C: set_waiting_human    设置current_step为“waiting_human”准备进入中断节点
节点 D: human_interrupt     调用interrupt中断状态机的执行
节点 E: assemble_and_submit API 组装与执行
节点 F: respond             响应与回调监听入口
"""
from __future__ import annotations

import logging
import re
from typing import Any

from langgraph.types import interrupt

from app.services.video_graph.event_bus import publish_event
from app.services.video_graph.state import (
    DEFAULT_CONFIG,
    ConfigParams,
    ScriptSegment,
    VideoGenerationState,
)
from app.services.video_graph.view_state import format_segments_for_view

logger = logging.getLogger(__name__)

MAX_SEGMENT_DURATION = 12
MIN_SEGMENT_DURATION = 4  # seedance 1.5 pro 官方下限
MAX_REVISION_COUNT = 10  # 视频脚本最大修改轮次


# ──────────────────────────────────────────────
# 节点 A：意图识别与参数提取
# ──────────────────────────────────────────────
async def parse_intent(state: VideoGenerationState) -> dict[str, Any]:
    """
    解析用户输入，确定 generation_mode、提取 config_params、
    验证图片 URL、处理音频对话格式。
    """
    thread_id = state.get("thread_id", "")
    # 就算前端此时还没有建立SSE连接也没关系。前端只需要显示当前正在进行的事件。
    # 只需要显示连接上的时候，正在进行的事件即可。前面的事件丢失了也没关系
    #因为不是前面的事件不是实时事件是旧事件，没必要显示
    await publish_event(thread_id, "progress", {
        "progress": 8,
        "message": "正在识别需求与参数…",
        "step": "parse_intent_running",
    })

    mode = state.get("generation_mode", "text_to_video")
    media = state.get("media_assets") or {}
    user_config = state.get("config_params") or {}

    merged_config: ConfigParams = {**DEFAULT_CONFIG, **{k: v for k, v in user_config.items() if v is not None}}

    if mode in ("image_to_video", "frame_interpolation"):
        ref_image_urls = media.get("ref_image_urls") or []
        first_frame_url = media.get("first_frame_url")
        last_frame_url = media.get("last_frame_url")

        has_images = bool(ref_image_urls or first_frame_url)
        if not has_images:
            err = f"模式 {mode} 需要提供图片 URL，请上传图片后重试。"
            await publish_event(thread_id, "error", {"message": err})
            return {"current_step": "error", "error": err}
    else:
        ref_image_urls = []
        first_frame_url = None
        last_frame_url = None

    parsed_media = {
        "ref_image_urls": ref_image_urls,
        "first_frame_url": first_frame_url or "",
        "last_frame_url": last_frame_url or "",
    }

    audio_fixed = False
    generate_audio = merged_config.get("generate_audio", True)
    user_input = state.get("user_input", "")
    if generate_audio and user_input:
        fixed = _fix_dialogue_quotes(user_input)
        if fixed != user_input:
            audio_fixed = True
            user_input = fixed

    await publish_event(thread_id, "progress", {
        "progress": 20,
        "message": "参数解析完成，开始构思分镜…",
        "step": "parse_intent_done",
    })

    return {
        "parsed_mode": mode,
        "parsed_config": merged_config,
        "parsed_media": parsed_media,
        "audio_prompt_fixed": audio_fixed,
        "user_input": user_input,
        "current_step": "parse_intent_done",
    }

# 只对英文文本生效
def _fix_dialogue_quotes(text: str) -> str:
    """标准化引号并尽量将未加引号的对白包裹为英文双引号。"""
    fixed = text.replace("\u201c", '"').replace("\u201d", '"')
    fixed = fixed.replace("\u300c", '"').replace("\u300d", '"')

    # He says hello -> He says "hello"
    fixed = re.sub(
        r'\b(says|said|asks|asked|replies|replied|shouts|shouted|whispers|whispered)\s+([^"\n.!?]+)([.!?]|$)',
        lambda m: f'{m.group(1)} "{m.group(2).strip()}"{m.group(3)}',
        fixed,
        flags=re.IGNORECASE,
    )
    return fixed


def _looks_like_english(text: str) -> bool:
    """粗粒度判定文本是否主要为英文，避免中文 prompt 直接提交模型。"""
    if not text:
        return False
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_chars = len(re.findall(r"[A-Za-z]", text))
    return zh_chars == 0 or en_chars >= zh_chars


async def _prepare_segment_for_submission(
    segment: ScriptSegment,
    *,
    language: str,
    generate_audio: bool,
) -> ScriptSegment:
    """统一规范单个片段：时长、英文 prompt、音频对白引号。"""
    from app.services.video_graph.llm_utils import translate_to_english

    normalized = dict(segment)
    duration = normalized.get("duration", 5)
    if not isinstance(duration, int):
        duration = 5
    normalized["duration"] = max(MIN_SEGMENT_DURATION, min(duration, MAX_SEGMENT_DURATION))

    desc = (normalized.get("description") or "").strip()
    desc_zh = (normalized.get("description_zh") or "").strip()

    if generate_audio:
        if desc:
            desc = _fix_dialogue_quotes(desc)

    if not _looks_like_english(desc):
        source = desc_zh or desc
        if source:
            desc = await translate_to_english(source)
            if language == "zh" and not desc_zh:
                desc_zh = source

    normalized["description"] = desc
    if desc_zh:
        normalized["description_zh"] = desc_zh
    return normalized


# ──────────────────────────────────────────────
# 节点 B：剧情规划与 Prompt 工程
# ──────────────────────────────────────────────
async def plan_script(state: VideoGenerationState) -> dict[str, Any]:
    """
    调用 LLM 将用户意图转化为结构化剧本（script_segments）。
    长视频（>12s）自动拆分为多个片段，每段 ≤12s。
    """
    from app.services.video_graph.llm_utils import call_script_planner

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 35,
        "message": "AI 正在构思视频分镜…",
        "step": "plan_script_running",
    })

    trend = state.get("trend", {})
    brand = state.get("brand", {})
    product = state.get("product", {})
    user_input = state.get("user_input", "")
    config = state.get("parsed_config", DEFAULT_CONFIG)
    mode = state.get("parsed_mode", "text_to_video")
    media = state.get("parsed_media", {})

    generate_audio = config.get("generate_audio", True)
    language = config.get("language", "zh")

    try:
        result = await call_script_planner(
            trend=trend,
            brand=brand,
            product=product,
            user_input=user_input,
            mode=mode,
            media=media,
            language=language,
            generate_audio=generate_audio,
        )
    except Exception as e:
        logger.exception("LLM 剧情规划失败")
        await publish_event(thread_id, "error", {"message": f"剧情规划失败: {e}"})
        return {"current_step": "error", "error": f"剧情规划失败: {e}"}

    await publish_event(thread_id, "progress", {
        "progress": 48,
        "message": "剧本草稿已生成，正在做标准化与翻译…",
        "step": "plan_script_running",
    })

    segments: list[ScriptSegment] = result.get("segments", [])
    normalized_segments: list[ScriptSegment] = []
    try:
        for idx, seg in enumerate(segments, start=1):
            normalized_segments.append(
                await _prepare_segment_for_submission(
                    seg,
                    language=language,
                    generate_audio=generate_audio,
                )
            )
            # # 颗粒度更细的进度：每个片段完成后广播一次--没必要
            # await publish_event(thread_id, "segment_done", {
            #     "index": idx,
            #     "total": len(segments),
            # })
    except Exception as e:
        logger.exception("剧本标准化失败")
        await publish_event(thread_id, "error", {"message": f"剧本标准化失败: {e}"})
        return {"current_step": "error", "error": f"剧本标准化失败: {e}"}

    total_dur = sum(s.get("duration", 5) for s in normalized_segments) #把所有片段的时长加起来计算出总时长
    has_frame_dep = any( #如果片段中有frame_interpolation模式，则执行策略为sequential，否则为parallel
        s.get("mode") == "frame_interpolation" for s in normalized_segments[1:]
    )
    strategy = "sequential" if has_frame_dep else "parallel"

    await publish_event(thread_id, "progress", {
        "progress": 55,
        "message": "分镜草稿生成完毕，等待您审阅",
        "step": "plan_script_done",
    })

    return {
        "script_segments": normalized_segments,
        "total_duration": total_dur,
        "execution_strategy": strategy,
        "optimized_prompt": result.get("optimized_prompt", ""),
        "current_step": "plan_script_done", #这里的step不是节点名称，而是后端任务状态。
        "revision_count": state.get("revision_count", 0),
    }


# ──────────────────────────────────────────────
# 节点 C：人机交互（interrupt 前的状态转换）
# ──────────────────────────────────────────────
async def set_waiting_human(state: VideoGenerationState) -> dict[str, Any]:
    """
    将curent_step设置为waiting_human，等待人类输入。
    """

    return {
        "current_step": "waiting_human",
        "human_action": None,
    }

def human_interrupt(state: VideoGenerationState) -> dict:
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
            "duration": seg.get("duration"),
            "mode": seg.get("mode"),
        })

    # 调用interrupt函数，langgraph状态机终止执行。interrupt里面是返回给前端的字段。
    # 前端传回的值会存到human_input里。
    human_input = interrupt({
        "event": "require_human_input",
        "segments": display_segments,
        "total_duration": state.get("total_duration", 0),
        "execution_strategy": state.get("execution_strategy", "parallel"),
        "revision_count": state.get("revision_count", 0),
        "message": "请审阅剧本，选择: approve(确认生成) / edit(修改后生成) / feedback(提出意见重新生成)",
    })

    # return的值会传递给路由函数
    return {
        "human_action": human_input.get("human_action", "approve"),
        "human_edited_segments": human_input.get("human_edited_segments", []),
        "human_feedback": human_input.get("human_feedback", ""),
        "current_step": "human_responded",
    }
# ──────────────────────────────────────────────
# 人类响应路由（条件边函数）
# ──────────────────────────────────────────────
def route_human_action(state: VideoGenerationState) -> str:
    """根据 human_action 决定下一个节点。"""
    action = state.get("human_action")
    if action == "approve":
        return "assemble_and_submit"
    elif action == "edit":
        return "apply_edit"
    elif action == "feedback":
        return "revise_script"
    return "set_waiting_human"


# ──────────────────────────────────────────────
# 编辑应用（用户直接修改 segment）
# ──────────────────────────────────────────────
async def apply_edit(state: VideoGenerationState) -> dict[str, Any]:
    """用户手动修改了部分 segment，直接替换。"""
    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 62,
        "message": "正在应用您的编辑…",
        "step": "apply_edit_running",
    })

    edited = state.get("human_edited_segments")
    if not edited:
        return {"current_step": "waiting_human"}

    current_segments = list(state.get("script_segments", []))
    edited_map = {s["segment_id"]: s for s in edited if "segment_id" in s}
    config = state.get("parsed_config", DEFAULT_CONFIG)
    generate_audio = config.get("generate_audio", True)
    language = config.get("language", "zh")
    for i, seg in enumerate(current_segments):
        sid = seg.get("segment_id")
        if sid in edited_map:
            merged = {**seg, **edited_map[sid]}
            try:
                current_segments[i] = await _prepare_segment_for_submission(
                    merged,
                    language=language,
                    generate_audio=generate_audio,
                )
            except Exception as e:
                logger.exception("用户编辑片段标准化失败: segment=%s", sid)
                await publish_event(thread_id, "error", {
                    "message": f"编辑片段标准化失败(segment={sid}): {e}"
                })
                return {
                    "current_step": "error",
                    "error": f"编辑片段标准化失败(segment={sid}): {e}",
                }

    total_dur = sum(s.get("duration", 5) for s in current_segments)
    return {
        "script_segments": current_segments,
        "total_duration": total_dur,
        "current_step": "plan_script_done",
    }


# ──────────────────────────────────────────────
# 重写剧本（用户给反馈，LLM 重新生成）
# ──────────────────────────────────────────────
async def revise_script(state: VideoGenerationState) -> dict[str, Any]:
    """根据用户反馈让 LLM 重写指定 segment 或整体。"""
    from app.services.video_graph.llm_utils import call_script_reviser

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 70,
        "message": "AI 正在根据您的反馈重写剧本…",
        "step": "revise_script_running",
    })

    feedback = state.get("human_feedback", "")
    segments = state.get("script_segments", [])
    revision_count = state.get("revision_count", 0)

    if revision_count >= MAX_REVISION_COUNT:
        err = f"已达到最大修改轮次({MAX_REVISION_COUNT}次)，请直接编辑后确认。"
        return {"current_step": "error", "error": err}

    config = state.get("parsed_config", DEFAULT_CONFIG)
    generate_audio = config.get("generate_audio", True)
    language = config.get("language", "zh")

    try:
        revised = await call_script_reviser(
            segments=segments,
            feedback=feedback,
            trend=state.get("trend", {}),
            brand=state.get("brand", {}),
            product=state.get("product", {}),
            language=language,
            generate_audio=generate_audio,
        )
    except Exception as e:
        logger.exception("LLM 剧本修改失败")
        return {"current_step": "error", "error": f"剧本修改失败: {e}"}

    new_segments: list[ScriptSegment] = revised.get("segments", segments)
    normalized_segments: list[ScriptSegment] = []
    try:
        for idx, seg in enumerate(new_segments, start=1):
            normalized_segments.append(
                await _prepare_segment_for_submission(
                    seg,
                    language=language,
                    generate_audio=generate_audio,
                )
            )
    except Exception as e:
        logger.exception("重写后剧本标准化失败")
        return {"current_step": "error", "error": f"重写后剧本标准化失败: {e}"}

    total_dur = sum(s.get("duration", 5) for s in normalized_segments)
    has_frame_dep = any(
        s.get("mode") == "frame_interpolation" for s in normalized_segments[1:]
    )

    return {
        "script_segments": normalized_segments,
        "total_duration": total_dur,
        "execution_strategy": "sequential" if has_frame_dep else "parallel",
        "revision_count": revision_count + 1,
        "current_step": "plan_script_done",
    }


# ──────────────────────────────────────────────
# 节点 D：API 组装与执行
# ──────────────────────────────────────────────
async def assemble_and_submit(state: VideoGenerationState) -> dict[str, Any]:
    """
    根据 script_segments 构建 Seedance payload 并提交任务。
    按 execution_strategy 决定串行/并行执行。
    """
    import asyncio
    import json as _json
    from app.services.video_graph.payload_builder import build_payload_for_segment
    from app.services.seedance_client import create_seedance_video_task
    from app.db.redis import get_redis_client

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 85,
        "message": "正在向视频生成引擎提交任务…",
        "step": "assemble_and_submit_running",
    })

    segments = state.get("script_segments", [])
    config = state.get("parsed_config", DEFAULT_CONFIG)
    strategy = state.get("execution_strategy", "parallel")
    store_id = state.get("shopify_store_id", "")

    task_results = []

    if strategy == "sequential":
        first_seg = dict(segments[0]) if segments else {}
        if not first_seg:
            return {
                "task_results": [],
                "final_status": "failed",
                "error": "无剧本片段可提交",
                "current_step": "error",
            }

        payload = build_payload_for_segment(first_seg, config)
        try:
            result = await create_seedance_video_task(payload)
            task_id = result.get("id", "")
            task_results.append({
                "segment_id": first_seg.get("segment_id"),
                "task_id": task_id,
                "status": result.get("status", "submitted"),
                "prompt": first_seg.get("description", ""),
            })

            if task_id and len(segments) > 1:
                redis_client = get_redis_client()
                chain_data = {
                    "remaining_segments": [dict(s) for s in segments[1:]],
                    "config": dict(config),
                    "store_id": store_id,
                    "trend": state.get("trend", {}),
                    "brand": state.get("brand", {}),
                }
                await redis_client.set(
                    f"seq_chain:{task_id}",
                    _json.dumps(chain_data, ensure_ascii=False),
                    ex=86400,
                )
                logger.info(
                    "串行链条已存入 Redis: task_id=%s, 剩余 %d 段",
                    task_id, len(segments) - 1,
                )
        except Exception as e:
            logger.exception("Seedance 首段任务提交失败")
            task_results.append({
                "segment_id": first_seg.get("segment_id"),
                "task_id": "",
                "status": "failed",
                "prompt": first_seg.get("description", ""),
            })
    else:
        payloads = [build_payload_for_segment(seg, config) for seg in segments]

        async def _submit(seg: dict, payload: dict) -> dict:
            try:
                result = await create_seedance_video_task(payload)
                return {
                    "segment_id": seg.get("segment_id"),
                    "task_id": result.get("id", ""),
                    "status": result.get("status", "submitted"),
                    "prompt": seg.get("description", ""),
                }
            except Exception as e:
                logger.exception("Seedance 任务提交失败: segment=%s", seg.get("segment_id"))
                return {
                    "segment_id": seg.get("segment_id"),
                    "task_id": "",
                    "status": "failed",
                    "prompt": seg.get("description", ""),
                }

        task_results = await asyncio.gather(
            *[_submit(seg, pl) for seg, pl in zip(segments, payloads)]
        )
        task_results = list(task_results)

    all_ok = all(r.get("status") != "failed" for r in task_results)
    await publish_event(thread_id, "progress", {
        "progress": 95,
        "message": "任务已提交，正在登记生成记录…",
        "step": "submitted",
    })
    return {
        "task_results": task_results,
        "final_status": "submitted" if all_ok else "partial_failure",
        "current_step": "submitted",
    }


# ──────────────────────────────────────────────
# 节点 E：响应（写入 DB Generation 记录）
# ──────────────────────────────────────────────
async def respond(state: VideoGenerationState) -> dict[str, Any]:
    """
    将提交结果写入数据库 Generation 表，并通过 WS 推送初始状态。
    回调监听由已有的 /generate/callback 处理，不阻塞本节点。
    """
    from app.db.mysql import SessionLocal
    from app.models import Generation
    from app.services.notification_service import publish_generation_status

    task_results = state.get("task_results", [])
    store_id = state.get("shopify_store_id", "")
    thread_id = state.get("thread_id", "")
    trend = state.get("trend", {})
    brand = state.get("brand", {})
    segments = state.get("script_segments", [])

    prompt_summary = "\n---\n".join(
        f"[Segment {s.get('segment_id', '?')}] {s.get('description', '')}"
        for s in segments
    )

    generation_ids = []

    db = SessionLocal()
    try:
        for tr in task_results:
            if not tr.get("task_id"):
                continue
            gen = Generation(
                shopify_store_id=store_id,
                type="video",
                status="queued",
                thread_id=thread_id or None,
                prompt_used=tr.get("prompt", ""),
                trend_snapshot=trend,
                brand_snapshot=brand,
                external_id=tr["task_id"],
            )
            db.add(gen)
            db.commit()
            db.refresh(gen)
            generation_ids.append(gen.id)
            tr["generation_id"] = gen.id

            try:
                await publish_generation_status(
                    store_id=store_id,
                    generation_id=gen.id,
                    status="queued",
                )
            except Exception:
                logger.warning("WS 推送初始状态失败: generation_id=%s", gen.id)
    except Exception as e:
        logger.exception("写入 Generation 记录失败")
        return {
            "task_results": task_results,
            "final_status": "db_error",
            "error": str(e),
            "current_step": "done",
        }
    finally:
        db.close()

    final_status = state.get("final_status", "submitted")
    await publish_event(state.get("thread_id", ""), "progress", {
        "progress": 100,
        "message": "视频生成任务已成功提交",
        "step": "done",
    })
    return {
        "task_results": task_results,
        "final_status": final_status,
        "current_step": "done",
    }

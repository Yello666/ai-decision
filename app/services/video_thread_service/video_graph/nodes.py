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

import asyncio
import logging
import re
from typing import Any

from langgraph.types import interrupt

from app.services.video_thread_service.video_graph.event_bus import publish_event
from app.services.video_thread_service.video_graph.state import (
    DEFAULT_CONFIG,
    ConfigParams,
    ScriptSegment,
    VideoGenerationState,
)

logger = logging.getLogger(__name__)

MAX_SEGMENT_DURATION = 15
MIN_SEGMENT_DURATION = 4  # Seedance 2.0 官方下限
MAX_REVISION_COUNT = 10  # 视频脚本最大修改轮次
MEDIA_REFERENCE_TAG_RE = re.compile(r"\[(?:image|video|audio|图|视频|音频)\d+\]", re.IGNORECASE)


def _coerce_segment_duration_seconds(raw: Any, *, default: int = 5) -> int:
    """单段时长：-1 表示方舟 API 由模型自主选择；否则夹到 [MIN, MAX]。"""
    if raw == -1:
        return -1
    if not isinstance(raw, int):
        raw = default
    return max(MIN_SEGMENT_DURATION, min(raw, MAX_SEGMENT_DURATION))


def _total_duration_from_segments(segments: list[dict]) -> int | None:
    """任一段为自动时长 (-1) 时总时长未知，返回 None；否则返回各段时长之和。"""
    if not segments:
        return 0
    if any(s.get("duration") == -1 for s in segments):
        return None
    return sum(_coerce_segment_duration_seconds(s.get("duration", 5)) for s in segments)


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

    ref_image_urls = _as_str_list(media.get("ref_image_urls") or [])
    product_image_url = str((state.get("product_for_prompt") or {}).get("image_url") or "").strip()
    if product_image_url and product_image_url not in ref_image_urls:
        ref_image_urls = [product_image_url, *ref_image_urls]
    reference_video_urls = media.get("reference_video_urls") or []
    reference_audio_urls = media.get("reference_audio_urls") or []

    if mode not in {"text_to_video", "multimodal_reference"}:
        err = f"不支持的 Seedance2 生成模式: {mode}"
        await publish_event(thread_id, "error", {"message": err})
        return {"current_step": "error", "error": err}
    #只要有参考图参考视频参考音频这些参数，就视模型为"multimodal_reference"模式
    if ref_image_urls or reference_video_urls or reference_audio_urls:
        mode = "multimodal_reference"

    if mode == "multimodal_reference":
        has_image_or_video = bool(ref_image_urls or reference_video_urls)
        if reference_audio_urls and not has_image_or_video:
            err = "参考音频不能作为唯一多模态素材，请同时上传参考图或参考视频。"
            await publish_event(thread_id, "error", {"message": err})
            return {"current_step": "error", "error": err}
        if not has_image_or_video:
            err = f"模式 {mode} 需要提供参考图或参考视频，请上传素材后重试。"
            await publish_event(thread_id, "error", {"message": err})
            return {"current_step": "error", "error": err}
    elif mode == "text_to_video":
        # 纯文生入口不强依赖素材；若用户上传了素材，后续按多模态模式处理。
        pass

    parsed_media = {
        "ref_image_urls": ref_image_urls,
        "reference_video_urls": reference_video_urls,
        "reference_audio_urls": reference_audio_urls,
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


def _segment_has_media_reference(segment: dict) -> bool:
    description = " ".join(
        str(segment.get(key) or "")
        for key in ("description", "description_zh")
    )
    return bool(MEDIA_REFERENCE_TAG_RE.search(description))


def _enforce_seedance2_segment_modes(
    segments: list[ScriptSegment],
    *,
    mode: str,
    media: dict,
) -> list[ScriptSegment]:
    """确保分段模式符合 Seedance2 串行策略。"""
    normalized: list[ScriptSegment] = []
    for idx, segment in enumerate(segments):
        updated = dict(segment)
        old_mode = updated.get("mode")
        if _segment_has_media_reference(updated):
            updated["mode"] = "multimodal_reference"
        elif idx == 0:
            updated["mode"] = "text_to_video"
        else:
            updated["mode"] = "first_frame"
        if old_mode != updated["mode"]:
            logger.info(
                "enforce_seedance2_segment_modes segment %d: %s -> %s (has_ref=%s)",
                idx + 1, old_mode, updated["mode"],
                _segment_has_media_reference(updated),
            )
        normalized.append(updated)
    return normalized


async def _prepare_segment_for_submission(
    segment: ScriptSegment,
    *,
    language: str,
    generate_audio: bool,
) -> ScriptSegment:
    """统一规范单个片段：时长、英文 prompt、音频对白引号。"""
    from app.services.video_thread_service.video_graph.llm_utils import translate_to_english

    normalized = dict(segment)
    normalized["duration"] = _coerce_segment_duration_seconds(normalized.get("duration", 5))

    desc = (normalized.get("description") or "").strip()
    desc_zh = (normalized.get("description_zh") or "").strip()

    if generate_audio:
        if desc:
            desc = _fix_dialogue_quotes(desc)

    if not _looks_like_english(desc):
        source = desc or desc_zh
        if source:
            desc = await translate_to_english(source)
            if not desc_zh:
                desc_zh = source

    normalized["description"] = desc
    if desc_zh:
        normalized["description_zh"] = desc_zh
    return normalized


async def _attach_zh_translation(segment: ScriptSegment) -> ScriptSegment:
    """补齐中文审阅文案，已有文案优先保留。"""
    from app.services.video_thread_service.video_graph.llm_utils import translate_to_zh

    normalized = dict(segment)
    desc = (normalized.get("description") or "").strip()
    fallback_zh = (normalized.get("description_zh") or "").strip()

    if fallback_zh:
        return normalized
    if desc:
        description_zh = await translate_to_zh(desc)
    else:
        description_zh = ""

    if description_zh:
        normalized["description_zh"] = description_zh
    return normalized


async def _attach_zh_translations(segments: list[ScriptSegment]) -> list[ScriptSegment]:
    """并发翻译所有分镜，降低多段脚本的等待时间。"""
    if not segments:
        return []
    return list(await asyncio.gather(*(_attach_zh_translation(seg) for seg in segments)))


def _normalize_user_edited_segment(
    segment: ScriptSegment,
    *,
    language: str,
) -> ScriptSegment:
    """把前端可见的编辑字段转换为后端双语字段。"""
    normalized = dict(segment)
    description = str(normalized.get("description") or "").strip()
    description_zh = str(normalized.get("description_zh") or "").strip()

    if description and (language == "zh" or not _looks_like_english(description)):
        normalized["description_zh"] = description
    elif description_zh and "description" not in normalized:
        normalized["description"] = description_zh
    return normalized


# ──────────────────────────────────────────────
# 节点 B：剧情规划与 Prompt 工程
# ──────────────────────────────────────────────
async def plan_script(state: VideoGenerationState) -> dict[str, Any]:
    """
    调用 LLM 将用户意图转化为结构化剧本（script_segments）。
    长视频（>12s）自动拆分为多个片段，每段 ≤12s。
    """
    from app.services.video_thread_service.video_graph.llm_utils import call_script_planner

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 35,
        "message": "AI 正在构思视频分镜…",
        "step": "plan_script_running",
    })

    trend = state.get("trend", {})
    brand = state.get("brand", {})
    product_for_prompt = state.get("product_for_prompt") or state.get("product", {})
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
            product_for_prompt=product_for_prompt,
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

    total_dur = _total_duration_from_segments(normalized_segments)
    normalized_segments = _enforce_seedance2_segment_modes(
        normalized_segments,
        mode=mode,
        media=media,
    )
    try:
        normalized_segments = await _attach_zh_translations(normalized_segments)
    except Exception as e:
        logger.exception("剧本中文翻译失败")
        await publish_event(thread_id, "error", {"message": f"剧本中文翻译失败: {e}"})
        return {"current_step": "error", "error": f"剧本中文翻译失败: {e}"}

    strategy = "sequential"

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

async def human_interrupt(state: VideoGenerationState) -> dict:
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
        desc_zh = seg.get("description_zh")
        display_segments.append({
            "segment_id": seg.get("segment_id"),
            "description": desc_zh if lang == "zh" else seg.get("description"),
            "duration": seg.get("duration"),
            "mode": seg.get("mode"),
        })

    interrupt_payload = {
        "event": "require_human_input",
        "segments": display_segments,
        "total_duration": state.get("total_duration", 0),
        "execution_strategy": state.get("execution_strategy", "parallel"),
        "revision_count": state.get("revision_count", 0),
        "message": "请审阅剧本，选择: approve(确认生成) / edit(修改后生成) / feedback(提出意见重新生成)",
    }

    # 通过 SSE 主动推送一次，前端无需等待 resume 轮询就能拿到待审阅剧本
    await publish_event(
        state.get("thread_id", ""),
        "human_action_required",
        interrupt_payload,
    )

    # 调用interrupt函数，langgraph状态机终止执行。interrupt里面是返回给前端的字段。
    # 前端传回的值会存到human_input里。
    human_input = interrupt(interrupt_payload)

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
    from app.services.video_thread_service.video_graph.view_state import format_segments_for_view

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 62,
        "message": "正在应用您的编辑…",
        "step": "apply_edit_running",
    })

    edited = state.get("human_edited_segments")
    logger.info(
        "apply_edit: 收到 %d 条编辑, segment_ids=%s",
        len(edited) if edited else 0,
        [s.get("segment_id") for s in (edited or [])],
    )
    if not edited:
        logger.warning("apply_edit: edited_segments 为空, 跳过编辑")
        await publish_event(thread_id, "warning", {
            "message": "未收到编辑内容，请检查请求参数",
        })
        return {"current_step": "waiting_human"}
    logger.info(f"编辑后的内容:{edited}")
    current_segments = list(state.get("script_segments", []))
    config = state.get("parsed_config", DEFAULT_CONFIG)
    generate_audio = config.get("generate_audio", True)
    language = config.get("language", "zh")
    edited_map = {
        s["segment_id"]: _normalize_user_edited_segment(s, language=language)
        for s in edited
        if "segment_id" in s
    }
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

    mode = state.get("parsed_mode", "text_to_video")
    media = state.get("parsed_media", {})
    current_segments = _enforce_seedance2_segment_modes(
        current_segments,
        mode=mode,
        media=media,
    )
    try:
        current_segments = await _attach_zh_translations(current_segments)
    except Exception as e:
        logger.exception("编辑后剧本中文翻译失败")
        await publish_event(thread_id, "error", {"message": f"编辑后剧本中文翻译失败: {e}"})
        return {"current_step": "error", "error": f"编辑后剧本中文翻译失败: {e}"}

    total_dur = _total_duration_from_segments(current_segments)

    await publish_event(thread_id, "segments_updated", {
        "message": "编辑已应用",
        "step": "apply_edit_done",
        "segments": format_segments_for_view(current_segments, language),
        "total_duration": total_dur,
    })

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
    from app.services.video_thread_service.video_graph.llm_utils import call_script_reviser

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
            media=state.get("parsed_media", {}),
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

    total_dur = _total_duration_from_segments(normalized_segments)
    mode = state.get("parsed_mode", "text_to_video")
    media = state.get("parsed_media", {})
    normalized_segments = _enforce_seedance2_segment_modes(
        normalized_segments,
        mode=mode,
        media=media,
    )
    try:
        normalized_segments = await _attach_zh_translations(normalized_segments)
    except Exception as e:
        logger.exception("重写后剧本中文翻译失败")
        return {"current_step": "error", "error": f"重写后剧本中文翻译失败: {e}"}

    return {
        "script_segments": normalized_segments,
        "total_duration": total_dur,
        "execution_strategy": "sequential",
        "revision_count": revision_count + 1,
        "current_step": "plan_script_done",
    }


# ──────────────────────────────────────────────
# 节点 D：API 组装与执行
# ──────────────────────────────────────────────
CALLBACK_URL = "https://shop-ai.xin/api/v1/video-thread/callback"


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_multimodal_prompt(prompt: str, *, has_continuity_frame: bool) -> str:
    if not has_continuity_frame:
        return prompt
    return (
        "The first frame of this video segment MUST match the last reference image "
        "(inherited from the previous segment's last frame); "
        "keep the same character, product placement, lighting, and visual style. "
        f"{prompt}"
    )


def build_payload_for_segment(
    segment: dict,
    config: dict,
    media: dict | None = None,
    *,
    continuity_image_url: str = "",
):
    """将单个脚本片段包装成 Seedance 2.0 请求对象。"""
    from app.schemas.seedance2 import Seedance2VideoRequest

    media = media or {}
    mode = segment.get("mode") or "text_to_video"
    prompt = (segment.get("description") or "").strip()
    if not prompt:
        raise ValueError("segment description 不能为空")

    duration = _coerce_segment_duration_seconds(segment.get("duration", 5))

    common: dict[str, Any] = {
        "prompt": prompt,
        "duration": duration,
        "ratio": config.get("ratio") or DEFAULT_CONFIG["ratio"],
        "resolution": config.get("resolution") or DEFAULT_CONFIG["resolution"],
        "watermark": config.get("watermark", DEFAULT_CONFIG["watermark"]),
        "generate_audio": config.get("generate_audio", DEFAULT_CONFIG["generate_audio"]),
        "callback_url": CALLBACK_URL,
        "return_last_frame": True,
    }

    if mode == "multimodal_reference":
        base_images = _as_str_list(
            segment.get("reference_image_urls")
            or segment.get("image_urls")
            or media.get("ref_image_urls")
        )
        reference_videos = _as_str_list(
            segment.get("reference_video_urls") or media.get("reference_video_urls")
        )
        reference_audio = _as_str_list(
            segment.get("reference_audio_urls") or media.get("reference_audio_urls")
        )
        has_continuity_frame = bool(continuity_image_url)
        reference_images = (
            [*base_images, continuity_image_url][:9]
            if has_continuity_frame
            else base_images[:9]
        )
        if not (reference_images or reference_videos):
            raise ValueError("multimodal_reference 模式需要至少包含参考图或参考视频")
        common["prompt"] = _build_multimodal_prompt(
            prompt,
            has_continuity_frame=has_continuity_frame,
        )
        return Seedance2VideoRequest(
            mode="multimodal_reference",
            reference_image_urls=reference_images,
            reference_video_urls=reference_videos[:3],
            reference_audio_urls=reference_audio[:3],
            **common,
        )

    if mode == "first_frame":
        if not continuity_image_url:
            raise ValueError("first_frame 模式缺少延续参考图")
        common["prompt"] = _build_multimodal_prompt(
            prompt,
            has_continuity_frame=True,
        )
        return Seedance2VideoRequest(
            mode="multimodal_reference",
            reference_image_urls=[continuity_image_url],
            **common,
        )

    return Seedance2VideoRequest(mode="text_to_video", **common)


async def create_seedance_video_task(req) -> dict[str, Any]:
    """提交 Seedance 2.0 视频任务，保留旧节点调用名以降低改动面。"""
    from app.services.seedance_service import create_video_task

    return await create_video_task(req)


async def assemble_and_submit(state: VideoGenerationState) -> dict[str, Any]:
    """
    根据 script_segments 构建 Seedance payload 并提交任务。
    按 execution_strategy 决定串行/并行执行。
    """
    import json as _json
    from app.db.redis import get_redis_client

    thread_id = state.get("thread_id", "")
    await publish_event(thread_id, "progress", {
        "progress": 85,
        "message": "正在向视频生成引擎提交任务…",
        "step": "assemble_and_submit_running",
    })

    segments = state.get("script_segments", [])
    config = state.get("parsed_config", DEFAULT_CONFIG)
    store_id = state.get("shopify_store_id", "")
    media = state.get("parsed_media") or {}

    task_results = []

    first_seg = dict(segments[0]) if segments else {}
    if not first_seg:
        return {
            "task_results": [],
            "final_status": "failed",
            "error": "无剧本片段可提交",
            "current_step": "error",
        }

    payload = build_payload_for_segment(first_seg, config, media)
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
                "media": dict(media),
                "store_id": store_id,
                "thread_id": thread_id,
                "trend": state.get("trend", {}),
                "brand": state.get("brand", {}),
            }
            await redis_client.set(
                f"seq_chain:{task_id}",
                _json.dumps(chain_data, ensure_ascii=False),
                ex=86400,
            )
            logger.info(
                "Seedance2 串行链条已存入 Redis: task_id=%s, 剩余 %d 段",
                task_id, len(segments) - 1,
            )
    except Exception:
        logger.exception("Seedance2 首段任务提交失败")
        task_results.append({
            "segment_id": first_seg.get("segment_id"),
            "task_id": "",
            "status": "failed",
            "prompt": first_seg.get("description", ""),
        })

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

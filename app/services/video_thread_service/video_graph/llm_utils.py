"""
LangGraph 视频生成编排系统 —— LLM 调用工具。

负责：
- call_script_planner: 将用户意图转化为结构化剧本 + 英文 prompt
- call_script_reviser: 根据用户反馈修改剧本
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.cost_log import log_llm_usage

logger = logging.getLogger(__name__)

MAX_SEGMENT_DURATION = 15
MIN_SEGMENT_DURATION = 4
LLM_REQUEST_TIMEOUT_SECONDS = 180
LLM_MAX_RETRIES = 2
PLANNER_MAX_TOKENS = 4000
REVISER_MAX_TOKENS = 4000
TRANSLATE_MAX_TOKENS = 1000
MEDIA_REFERENCE_TAG_RE = re.compile(r"\[(?:image|video|audio|图|视频|音频)\d+\]", re.IGNORECASE)


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _format_available_media_references(
    media: dict[str, Any],
    product: dict[str, Any] | None = None,
) -> str:
    """把可用素材转换成 LLM 必须引用的稳定标签清单。

    仅向 LLM 暴露稳定标签与语义说明，URL 由后续真实生成时再注入，避免长 URL 浪费 token。
    """
    lines: list[str] = []
    product = product or {}
    product_image_url = str(product.get("image_url") or "").strip()
    product_name = str(product.get("name") or "the product").strip()
    for idx, url in enumerate(_as_str_list(media.get("ref_image_urls")), start=1):
        if product_image_url and url == product_image_url:
            lines.append(f"- [image{idx}]: product reference image for {product_name}")
        else:
            lines.append(f"- [image{idx}]: user-provided reference image")
    for idx, _url in enumerate(_as_str_list(media.get("reference_video_urls")), start=1):
        lines.append(f"- [video{idx}]: user-provided reference video")
    for idx, _url in enumerate(_as_str_list(media.get("reference_audio_urls")), start=1):
        lines.append(f"- [audio{idx}]: user-provided reference audio")
    return "\n".join(lines) if lines else "(none)"


def _get_llm_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.LLM_API_KEY:
        raise ValueError("未配置 LLM_API_KEY")
    return AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_API_URL,
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
        http_client=httpx.AsyncClient(proxy=None),
    )


def _chat_extra_kwargs(*, max_tokens: int) -> dict[str, Any]:
    """统一 LLM 请求参数，减少长思考导致的超时与高 token 消耗。"""
    return {
        "max_tokens": max_tokens,
        "timeout": LLM_REQUEST_TIMEOUT_SECONDS,
        "extra_body": {"enable_thinking": False},
    }


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象（兼容 markdown code fence）。"""
    patterns = [
        r"```json\s*([\s\S]*?)```",
        r"```\s*([\s\S]*?)```",
        r"(\{[\s\S]*\})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法从 LLM 输出中提取有效 JSON: {text[:200]}")


def _normalize_segment_mode(seg: dict[str, Any], index: int) -> str:
    """按单段 prompt 是否显式引用参考素材，确定实际生成模式。"""
    description = str(seg.get("description") or "")
    if MEDIA_REFERENCE_TAG_RE.search(description):
        return "multimodal_reference"
    if index == 0:
        return "text_to_video"
    return "first_frame"


def _inject_missing_product_refs(
    segments: list[dict],
    media: dict,
    product: dict,
) -> list[dict]:
    """如果有产品参考图但 LLM 完全没引用，自动注入 [image1] 标签。"""
    product_image = (product.get("image_url") or "").strip()
    ref_images = media.get("ref_image_urls") or []
    if not product_image or product_image not in ref_images:
        return segments

    any_has_ref = any(
        MEDIA_REFERENCE_TAG_RE.search(s.get("description", ""))
        for s in segments
    )
    if any_has_ref:
        return segments

    product_name = product.get("name", "the product")
    for seg in segments:
        desc = seg.get("description", "")
        seg["description"] = (
            f"{desc} The product shown is [image1] ({product_name}), "
            f"matching the exact appearance of [image1]."
        )
    logger.warning(
        "LLM 未嵌入任何媒体标签，已自动为 %d 个 segment 注入 [image1]",
        len(segments),
    )
    return segments


# ──────────────────────────────────────────────
# 剧情规划（节点 B）
# ──────────────────────────────────────────────
#第一段，如果没有参考音频，就是文字生视频。其他段如果没有用户没有说xx参考什么什么，就是首帧生成。
# 如果有参考图就是多模态生成（因为既要首帧生成），又要参考图生成。现在全部都是多模态生成。
PLANNER_SYSTEM_PROMPT = """\
## ROLE
You are a senior direct-response video strategist and Seedance 2.0 prompt engineer. Your task is to generate a multi-segment marketing video script that follows a strict technical execution logic based on the user's Default Mode.

## MARKETING STRATEGY
- Build the story around the hotspot/trend, but make the product the emotional or practical payoff.
- Use the brand tone and product benefits as constraints, not as a separate ad read.
- Open with a visual hook in segment 1, escalate through clear product use or transformation, and end with a memorable product/brand beat.

## SEGMENT STRUCTURE
Each segment must include:
- segment_id: int, starting from 1
- description: English prompt for the video model (vivid, cinematic, with camera/lighting details)
- duration: int, either -1 (model chooses length per Seedance API) or in [4, 15] seconds
- mode: one of text_to_video, first_frame, multimodal_reference

## USER INPUT (HIGH PRIORITY — MUST FOLLOW)
The "[User Input]" field contains the user's creative brief, instructions, and constraints.
- It may be in Chinese or English. Regardless of language, you MUST faithfully follow its intent.
- If the user mentions media labels such as [图1], [图2], [视频1], [音频1], these are references to assets listed in "[Available Media References]". You MUST normalize them to English labels ([image1], [image2], [video1], [audio1]) and embed them in the corresponding segment descriptions.
  Example: User says "[图1] 是我的吸尘器" → you MUST use [image1] in every segment that shows the product.
- If the user describes how specific assets should be used, follow those instructions precisely.

## MEDIA REFERENCE RULES (HIGHEST PRIORITY — READ FIRST)
- The user message includes a "[Available Media References]" block with English labels: [image1], [image2], [video1], [audio1], etc.
- The user idea may instead use Chinese variants. They refer to the SAME asset and you MUST normalize them to the English label in your output:
  [图N] == [imageN]   [视频N] == [videoN]   [音频N] == [audioN]
- HARD RULE 1 (PRODUCT IMAGE): If the references include a "product reference image" (typically [image1]), then BY DEFAULT every segment MUST embed [image1] inline in its description. The ONLY exception is a segment that intentionally does NOT show the product (e.g., a pure mood/atmosphere shot or a setup scene before the product reveal). Mentioning only the product name without [image1] is ALWAYS forbidden when [image1] exists.
  - Bad:  "the vacuum cleaner on the counter"
  - Good: "the [image1] vacuum cleaner on the counter, matching the exact appearance of [image1]"
- HARD RULE 2: If the user idea explicitly references an asset (e.g., "[图1] 是我的吸尘器"), the corresponding English label ([image1]) MUST appear in at least one segment description.
- HARD RULE 3: A segment's description may also reference [video1]/[audio1] when those assets are needed; otherwise omit them.

## CORE EXECUTION LOGIC (apply AFTER following the rules above)
Determine each segment's mode in this strict order:
1) If the segment's description contains ANY reference label ([imageN], [videoN], or [audioN]) → mode = "multimodal_reference".
2) Else if it is segment 1 → mode = "text_to_video".
3) Else (segment 2+) → mode = "first_frame" (inherits the last frame of the previous clip for visual continuity).
This means: whenever a reference asset is available and the segment visually depicts it, you MUST embed the label AND set mode to "multimodal_reference" — regardless of segment position.

The user-provided default_mode is only a hint for segment 1 when no reference asset is needed:
- default_mode = text_to_video → segment 1 has no labels and uses text_to_video; later segments default to first_frame unless they embed labels.
- default_mode = multimodal_reference → prefer embedding labels and using multimodal_reference whenever it makes the visual stronger.

Constraints: Each segment duration is -1 (auto) or in [4, 15] seconds. Total English prompts must be under 1000 words.

## PROMPT WRITING RULES
- Specificity: Detail the subject appearance, product appearance, motion, camera angle (close-up, tracking, handheld, macro, etc.), lighting, and background.
- Temporal Progression: Describe what happens at the start vs. the end of the clip.
- Language: Use concrete, cinematic language. Avoid vague adjectives like beautiful.
- Audio: Dialogue must be wrapped in English double quotes. Example: The man turns to the camera and says "Remember the plan."
- Continuity: Each segment must focus on ONE action. For first_frame mode, briefly restate the subject to maintain consistency.

## OUTPUT FORMAT (Strict JSON)
The output must be a single JSON object. Whenever a referenced asset is depicted, embed its English label inline (see example).
{
  "optimized_prompt": "A single English paragraph summarizing the overall video concept.",
  "segments": [
    {
      "segment_id": 1,
      "description": "Cinematic close-up of a sunlit kitchen counter with the [image1] vacuum cleaner prominently placed, matching the exact shape and color of [image1] ...",
      "duration": 6,
      "mode": "multimodal_reference"
    },
    {
      "segment_id": 2,
      "description": "Medium shot, the [image1] vacuum cleaner sliding across the floor, matching the exact silhouette and color of [image1] ...",
      "duration": 8,
      "mode": "multimodal_reference"
    }
  ]
}
"""


async def call_script_planner(
    *,
    trend: dict,
    brand: dict,
    product_for_prompt: dict,
    user_input: str,
    mode: str,
    media: dict,
    language: str,
    generate_audio: bool,
) -> dict[str, Any]:
    """调用 LLM 生成结构化剧本。"""
    settings = get_settings()

    user_msg_parts = []
    user_msg_parts.append(f"[Trend] title: {trend.get('title', '')}, summary: {trend.get('summary', '')}, tags: {trend.get('tags', [])}")
    user_msg_parts.append(f"[Brand] name: {brand.get('name', '')}, tone: {brand.get('tone', '')}")
    if brand.get("core_value"):
        user_msg_parts.append(f"[Brand Slogan] {brand['core_value']}")
    user_msg_parts.append(f"[Product] {product_for_prompt.get('name', '')}: {product_for_prompt.get('description', '')}, price: {product_for_prompt.get('price', '')}$")
    user_msg_parts.append(
        f"[Available Media References]\n{_format_available_media_references(media, product_for_prompt)}"
    )
    user_msg_parts.append(f"[Default Mode] {mode}")
    user_msg_parts.append(
        f"[User Input — HIGH PRIORITY, follow strictly]\n"
        f"{user_input or '(no specific idea, use your creativity)'}"
    )
    user_msg = "\n".join(user_msg_parts)

    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_V4_DEEPSEEK,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
        **_chat_extra_kwargs(max_tokens=PLANNER_MAX_TOKENS),
    )

    log_llm_usage(
        "LLM生成视频脚本",
        resp.usage if hasattr(resp, "usage") else None,
        model=settings.LLM_MODEL_V4_DEEPSEEK,
    )

    content = resp.choices[0].message.content if resp.choices else ""
    if not content:
        raise ValueError("LLM 返回空内容")

    result = _extract_json(content)

    segments = result.get("segments", [])
    if not segments:
        raise ValueError("LLM 未返回任何剧本片段")

    for i, seg in enumerate(segments):
        logger.info(
            "LLM 原始输出 segment %d: mode=%s, has_ref=%s, desc=%.120s",
            i + 1,
            seg.get("mode"),
            bool(MEDIA_REFERENCE_TAG_RE.search(seg.get("description", ""))),
            seg.get("description", ""),
        )

    segments = _inject_missing_product_refs(segments, media, product_for_prompt)

    for i, seg in enumerate(segments):
        seg.setdefault("segment_id", i + 1)
        old_mode = seg.get("mode")
        seg["mode"] = _normalize_segment_mode(seg, i)
        if old_mode != seg["mode"]:
            logger.info(
                "normalize_segment_mode segment %d: %s -> %s",
                i + 1, old_mode, seg["mode"],
            )
        seg.setdefault("duration", 5)
        dur = seg["duration"]
        if dur == -1:
            seg["duration"] = -1
        elif not isinstance(dur, int):
            seg["duration"] = max(MIN_SEGMENT_DURATION, min(5, MAX_SEGMENT_DURATION))
        else:
            seg["duration"] = max(MIN_SEGMENT_DURATION, min(dur, MAX_SEGMENT_DURATION))

    return result


# ──────────────────────────────────────────────
# 剧本修改（节点 C → 重写路径）
# ──────────────────────────────────────────────
REVISER_SYSTEM_PROMPT = """\
You are a senior storyboard director. The user has reviewed a video script and wants changes.
Revise the script based on their feedback while preserving the overall brand/trend context.

MEDIA REFERENCE RULES (HIGHEST PRIORITY):
- The user message includes "[Available Media References]" with English labels: [image1], [video1], [audio1], etc.
- Chinese variants from user feedback mean the same asset and MUST be normalized to the English label in your output:
  [图N] == [imageN]   [视频N] == [videoN]   [音频N] == [audioN]
- If references contain a "product reference image" (typically [image1]), EVERY segment that visually shows the product MUST embed [image1] in the description. Writing only the product name without [image1] is forbidden.
- Do not remove existing media reference labels unless the user's feedback explicitly asks to stop using that asset.

OTHER RULES:
1. Each segment's duration MUST be -1 (model auto) or an integer in [4, 15] seconds (Seedance 2.0).
2. Keep segment_id numbering consistent.
3. The English description is the actual prompt for the video generation model.
4. When generate_audio is true, dialogue MUST be wrapped in English double quotes.
5. Preserve segments the user didn't mention — only modify what they asked to change.
6. English prompts ≤ 1000 words.
7. Valid modes are text_to_video, first_frame, multimodal_reference. A segment whose description contains ANY reference label MUST use mode "multimodal_reference"; otherwise keep the existing mode (text_to_video for segment 1, first_frame for segment 2+).

OUTPUT FORMAT (strict JSON):
{
  "segments": [
    {
      "segment_id": 1,
      "description": "English prompt with camera angles, lighting, motion. If the product is on screen, include [image1] inline.",
      "duration": 8,
      "mode": "text_to_video"
    }
  ]
}
"""


TRANSLATE_SYSTEM_PROMPT = """\
You are a professional localization assistant for video generation prompts.
Translate the given text into natural, cinematic English suitable for a text-to-video model.

RULES:
1. Keep semantic meaning unchanged.
2. Preserve quoted dialogue using double quotes.
3. Output only translated English text, no markdown or explanation.
"""


TRANSLATE_TO_ZH_SYSTEM_PROMPT = """\
You are a professional localization assistant for video generation prompts.
Translate the given English video script into natural Simplified Chinese for user review.

RULES:
1. Keep semantic meaning unchanged.
2. Preserve media reference labels exactly, such as [image1], [video1], [audio1], [图1], [视频1], [音频1].
3. Preserve quoted dialogue, translating the dialogue content into Chinese when appropriate.
4. Output only translated Simplified Chinese text, no markdown or explanation.
"""


async def call_script_reviser(
    *,
    segments: list[dict],
    feedback: str,
    trend: dict,
    brand: dict,
    product: dict,
    media: dict,
    language: str,
    generate_audio: bool,
) -> dict[str, Any]:
    """根据用户反馈修改剧本。"""
    settings = get_settings()

    current_script = json.dumps(segments, ensure_ascii=False, indent=2)
    user_msg = (
        f"[Current Script]\n{current_script}\n\n"
        f"[User Feedback] {feedback}\n\n"
        f"[Context] Trend: {trend.get('title', '')}, Brand: {brand.get('name', '')} ({brand.get('tone', '')}), "
        f"Product: {product.get('name', '')}\n"
        f"[Available Media References]\n{_format_available_media_references(media, product)}\n\n"
        f"[Generate Audio] {generate_audio}"
    )

    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_V4_DEEPSEEK,
        messages=[
            {"role": "system", "content": REVISER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
        **_chat_extra_kwargs(max_tokens=REVISER_MAX_TOKENS),
    )

    log_llm_usage(
        "LLM重新生成视频脚本",
        resp.usage if hasattr(resp, "usage") else None,
        model=settings.LLM_MODEL_V4_DEEPSEEK,
    )

    content = resp.choices[0].message.content if resp.choices else ""
    if not content:
        raise ValueError("LLM 修改返回空内容")

    return _extract_json(content)


async def translate_to_english(text: str) -> str:
    """将任意输入翻译为适合视频模型提交的英文 prompt 文本。"""
    src = (text or "").strip()
    if not src:
        return ""

    settings = get_settings()
    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_36_PLUS,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": src},
        ],
        temperature=0,
        **_chat_extra_kwargs(max_tokens=TRANSLATE_MAX_TOKENS),
    )
    log_llm_usage(
        "LLM视频脚本翻译",
        resp.usage if hasattr(resp, "usage") else None,
        model=settings.LLM_MODEL_36_PLUS,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    translated = (content or "").strip()
    if not translated:
        raise ValueError("翻译结果为空")
    return translated


async def translate_to_zh(text: str) -> str:
    """将英文视频脚本翻译为给用户审阅的中文文本。"""
    src = (text or "").strip()
    if not src:
        return ""

    settings = get_settings()
    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_36_PLUS,
        messages=[
            {"role": "system", "content": TRANSLATE_TO_ZH_SYSTEM_PROMPT},
            {"role": "user", "content": src},
        ],
        temperature=0,
        **_chat_extra_kwargs(max_tokens=TRANSLATE_MAX_TOKENS),
    )
    log_llm_usage(
        "LLM视频脚本翻译",
        resp.usage if hasattr(resp, "usage") else None,
        model=settings.LLM_MODEL_36_PLUS,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    translated = (content or "").strip()
    if not translated:
        raise ValueError("中文翻译结果为空")
    return translated

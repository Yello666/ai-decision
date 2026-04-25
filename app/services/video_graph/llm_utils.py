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

logger = logging.getLogger(__name__)

MAX_SEGMENT_DURATION = 15
MIN_SEGMENT_DURATION = 4
LLM_REQUEST_TIMEOUT_SECONDS = 180
LLM_MAX_RETRIES = 2
PLANNER_MAX_TOKENS = 4000
REVISER_MAX_TOKENS = 4000
TRANSLATE_MAX_TOKENS = 1000


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


# ──────────────────────────────────────────────
# 剧情规划（节点 B）
# ──────────────────────────────────────────────
PLANNER_SYSTEM_PROMPT = """\
You are a senior direct-response video strategist, storyboard director, and Seedance 2.0 prompt engineer.
Your task: combine the trend/hotspot, product facts, brand tone, and the user's idea into a segmented marketing video script optimized for Seedance 2.0.

## MARKETING STRATEGY
- Build the story around the hotspot/trend, but make the product the emotional or practical payoff.
- Use the brand tone and product benefits as constraints, not as a separate ad read.
- Open with a visual hook in segment 1, escalate through clear product use or transformation, and end with a memorable product/brand beat.
- Keep every segment self-contained enough for Seedance, while preserving character, product, scene, and style continuity across segments.

## MODEL CONSTRAINTS (Seedance 2.0)
- Duration per segment: integer in [4, 15] seconds.
- Supported modes for this workflow:
  - text_to_video: pure text generation.
  - first_frame: use the previous segment's returned last frame as this segment's first frame.
  - multimodal_reference: use reference images/videos/audio supplied by the user.
- Supported media references in prompts:
  - Images: [image1], [image2], ...
  - Videos: [video1], [video2], ...
  - Audio: [audio1], [audio2], ...
- English prompts should stay under 1000 words.

## PROMPT WRITING GUIDELINES (from Seedance official guide)
- Be specific about: subject appearance, product appearance, action/motion, camera angle (close-up, wide shot, tracking, handheld, macro, etc.), lighting, atmosphere, and background.
- Describe the temporal progression: what happens at the start vs. the end of the clip.
- Avoid vague adjectives like "beautiful" or "nice". Use concrete, cinematic and product-specific language.
- When generate_audio is true, dialogue MUST be wrapped in English double quotes.
  Example: The man turns to the camera and says "Remember, never point at the moon."
- Keep each segment's prompt focused on ONE scene/action. Complex stories → multiple segments.
- For multimodal_reference, explicitly describe how each referenced asset is used, e.g. [image1] is the product appearance anchor, [video1] provides camera rhythm, [audio1] provides voice or music mood.

## SEGMENT STRUCTURE
Each segment must include:
- segment_id: int, starting from 1
- description: English prompt for the video model (vivid, cinematic, with camera/lighting details)
- duration: int in [4, 15] seconds
- mode: one of text_to_video, first_frame, multimodal_reference

## CONTINUITY & EXECUTION STRATEGY
- Always plan for sequential execution.
- If Default Mode is text_to_video and no media references are available:
  - segment 1 MUST use text_to_video.
  - segment 2+ MUST use first_frame and continue from the previous segment's last frame.
- If Default Mode is multimodal_reference or media references are available:
  - ALL segments MUST use multimodal_reference.
  - Each segment prompt MUST mention the relevant references with [图n]/[视频n]/[音频n] and explain their role.
  - Segment 2+ should also continue visually from the previous segment's last frame; describe it as continuity from the prior shot.


## OUTPUT FORMAT (strict JSON)
{
  "optimized_prompt": "A single English paragraph summarizing the overall video concept.",
  "segments": [
    {
      "segment_id": 1,
      "description": "Detailed English prompt with camera angles, lighting, motion...",
      "duration": 8,
      "mode": "text_to_video"
    }
  ],
}
"""


async def call_script_planner(
    *,
    trend: dict,
    brand: dict,
    product: dict,
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
    user_msg_parts.append(f"[Brand] name: {brand.get('name', '')}, tone: {brand.get('tone', '')}, products: {brand.get('mainly_sold_products', brand.get('industry', ''))}")
    if brand.get("core_value"):
        user_msg_parts.append(f"[Brand Slogan] {brand['core_value']}")
    # TODO 这里就算product带了variant，也不会读取variant的信息（应该不管是product还是variant，都只提取name、description、price这些信息，再传入，）
    user_msg_parts.append(f"[Product] {product.get('name', '')}: {product.get('description', '')}, price: {product.get('price', '')}$")
    # TODO 这里会主动传递product的url，没必要，建议删除，剧情规划不依赖图片
    if product.get("image_url"):
        user_msg_parts.append(f"[Product Image] {product['image_url']}")
    user_msg_parts.append(f"[User Idea] {user_input or '(no specific idea, use your creativity)'}")
    user_msg_parts.append(f"[Default Mode] {mode}")
    user_msg_parts.append(f"[Generate Audio] {generate_audio}")

    if media.get("ref_image_urls"):
        user_msg_parts.append(f"[Reference Images as 图1..图N] {media['ref_image_urls']}")
    if media.get("reference_video_urls"):
        user_msg_parts.append(f"[Reference Videos as 视频1..视频N] {media['reference_video_urls']}")
    if media.get("reference_audio_urls"):
        user_msg_parts.append(f"[Reference Audio as 音频1..音频N] {media['reference_audio_urls']}")

    user_msg = "\n".join(user_msg_parts)

    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_35_PLUS,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
        **_chat_extra_kwargs(max_tokens=PLANNER_MAX_TOKENS),
    )

    content = resp.choices[0].message.content if resp.choices else ""
    if not content:
        raise ValueError("LLM 返回空内容")

    result = _extract_json(content)

    segments = result.get("segments", [])
    if not segments:
        raise ValueError("LLM 未返回任何剧本片段")

    has_multimodal_refs = bool(
        media.get("ref_image_urls")
        or media.get("reference_video_urls")
        or media.get("reference_audio_urls")
        or mode == "multimodal_reference"
    )

    for i, seg in enumerate(segments):
        seg.setdefault("segment_id", i + 1)
        if has_multimodal_refs:
            seg["mode"] = "multimodal_reference"
        elif i == 0:
            seg["mode"] = "text_to_video"
        else:
            seg["mode"] = "first_frame"
        seg.setdefault("duration", 5)
        dur = seg["duration"]
        if not isinstance(dur, int) or dur == -1:
            seg["duration"] = 5
        else:
            seg["duration"] = max(MIN_SEGMENT_DURATION, min(dur, MAX_SEGMENT_DURATION))

    return result


# ──────────────────────────────────────────────
# 剧本修改（节点 C → 重写路径）
# ──────────────────────────────────────────────
REVISER_SYSTEM_PROMPT = """\
You are a senior storyboard director. The user has reviewed a video script and wants changes.
Revise the script based on their feedback while preserving the overall brand/trend context.

RULES:
1. Each segment's duration MUST be an integer in [4, 15] seconds (Seedance 2.0 constraint).
2. Keep segment_id numbering consistent.
3. The English description is the actual prompt for the video generation model.
4. When generate_audio is true, dialogue MUST be in double quotes.
5. Preserve segments the user didn't mention — only modify what they asked to change.
6. English prompts ≤ 1000 words.
7. Valid modes are text_to_video, first_frame, multimodal_reference. Preserve the existing mode strategy unless the user explicitly asks otherwise.

OUTPUT FORMAT (strict JSON):
{
  "segments": [
    {
      "segment_id": 1,
      "description": "English prompt with camera angles, lighting, motion...",
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


async def call_script_reviser(
    *,
    segments: list[dict],
    feedback: str,
    trend: dict,
    brand: dict,
    product: dict,
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
        f"[Generate Audio] {generate_audio}"
    )

    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL_35_PLUS,
        messages=[
            {"role": "system", "content": REVISER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
        **_chat_extra_kwargs(max_tokens=REVISER_MAX_TOKENS),
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
        model=settings.LLM_MODEL_35_PLUS,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": src},
        ],
        temperature=0,
        **_chat_extra_kwargs(max_tokens=TRANSLATE_MAX_TOKENS),
    )
    content = resp.choices[0].message.content if resp.choices else ""
    translated = (content or "").strip()
    if not translated:
        raise ValueError("翻译结果为空")
    return translated

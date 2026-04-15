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

MAX_SEGMENT_DURATION = 12
MIN_SEGMENT_DURATION = 4


def _get_llm_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.LLM_API_KEY:
        raise ValueError("未配置 LLM_API_KEY")
    return AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_API_URL,
        timeout=90,
        max_retries=2,
        http_client=httpx.AsyncClient(proxy=None),
    )


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
You are a senior storyboard director and video prompt engineer specializing in Seedance 1.5 Pro.
Your task: convert the user's video idea into a structured shooting script optimized for the Seedance video generation model.

## MODEL CONSTRAINTS (Seedance 1.5 Pro)
- Duration per segment: integer in [4, 12] seconds. Use -1 to let the model decide.
- Supported modes: text_to_video (pure text), image_to_video (single image as first_frame), frame_interpolation (first_frame + last_frame for continuity).
- image_to_video with a single image uses role=first_frame (NOT reference_image — that is Seedance 2.0 only).
- Supports both Chinese and English prompts. English ≤ 1000 words; Chinese ≤ 500 characters.

## PROMPT WRITING GUIDELINES (from Seedance official guide)
- Be specific about: subject appearance, action/motion, camera angle (close-up, wide shot, tracking, etc.), lighting (golden hour, neon, studio), atmosphere/mood.
- Describe the temporal progression: what happens at the start vs. the end of the clip.
- Avoid vague adjectives like "beautiful" or "nice". Use concrete, cinematic language.
- When generate_audio is true, dialogue MUST be wrapped in English double quotes.
  Example: The man turns to the camera and says "Remember, never point at the moon."
- Keep each segment's prompt focused on ONE scene/action. Complex stories → multiple segments.

## SEGMENT STRUCTURE
Each segment must include:
- segment_id: int, starting from 1
- description: English prompt for the video model (vivid, cinematic, with camera/lighting details)
- description_zh: Chinese translation of the description (for user review)
- duration: int in [4, 12] seconds
- mode: one of text_to_video, image_to_video, frame_interpolation

## CONTINUITY & EXECUTION STRATEGY
- If segments need visual continuity (same character/scene across clips), use frame_interpolation for segments after the first, and set execution_strategy to "sequential".
- For independent segments, set execution_strategy to "parallel".

## OUTPUT FORMAT (strict JSON)
{
  "optimized_prompt": "A single English paragraph summarizing the overall video concept.",
  "segments": [
    {
      "segment_id": 1,
      "description": "Detailed English prompt with camera angles, lighting, motion...",
      "description_zh": "中文描述...",
      "duration": 8,
      "mode": "text_to_video"
    }
  ],
  "execution_strategy": "parallel"
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
    user_msg_parts.append(f"[Product] {product.get('name', '')}: {product.get('description', '')}, price: {product.get('price', '')}$")
    if product.get("image_url"):
        user_msg_parts.append(f"[Product Image] {product['image_url']}")
    user_msg_parts.append(f"[User Idea] {user_input or '(no specific idea, use your creativity)'}")
    user_msg_parts.append(f"[Default Mode] {mode}")
    user_msg_parts.append(f"[Generate Audio] {generate_audio}")

    if media.get("image_urls"):
        user_msg_parts.append(f"[Available Images] {media['image_urls']}")
    if media.get("first_frame_url"):
        user_msg_parts.append(f"[First Frame] {media['first_frame_url']}")

    user_msg = "\n".join(user_msg_parts)

    client = _get_llm_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content if resp.choices else ""
    if not content:
        raise ValueError("LLM 返回空内容")

    result = _extract_json(content)

    segments = result.get("segments", [])
    if not segments:
        raise ValueError("LLM 未返回任何剧本片段")

    for i, seg in enumerate(segments):
        seg.setdefault("segment_id", i + 1)
        seg.setdefault("mode", mode)
        seg.setdefault("duration", 5)
        dur = seg["duration"]
        if isinstance(dur, int) and dur != -1:
            seg["duration"] = max(MIN_SEGMENT_DURATION, min(dur, MAX_SEGMENT_DURATION))

    return result


# ──────────────────────────────────────────────
# 剧本修改（节点 C → 重写路径）
# ──────────────────────────────────────────────
REVISER_SYSTEM_PROMPT = """\
You are a senior storyboard director. The user has reviewed a video script and wants changes.
Revise the script based on their feedback while preserving the overall brand/trend context.

RULES:
1. Each segment's duration MUST be an integer in [4, 12] seconds (Seedance 1.5 Pro constraint).
2. Keep segment_id numbering consistent.
3. The English description is the actual prompt for the video generation model.
4. When generate_audio is true, dialogue MUST be in double quotes.
5. Preserve segments the user didn't mention — only modify what they asked to change.
6. English prompts ≤ 1000 words; Chinese prompts ≤ 500 characters.

OUTPUT FORMAT (strict JSON):
{
  "segments": [
    {
      "segment_id": 1,
      "description": "English prompt with camera angles, lighting, motion...",
      "description_zh": "中文描述...",
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
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": REVISER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
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
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": src},
        ],
        temperature=0,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    translated = (content or "").strip()
    if not translated:
        raise ValueError("翻译结果为空")
    return translated

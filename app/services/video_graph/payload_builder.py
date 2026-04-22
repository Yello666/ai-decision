"""
LangGraph 视频生成编排系统 —— Seedance Payload 构建器。

根据单个 ScriptSegment 和 ConfigParams 构建火山引擎方舟 API 请求体。
复用 trend_video_service 中的模型端点和回调地址。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MODEL_SEEDANCE_15_PRO = "ep-20260330165459-vmz9x"
MODEL_SEEDANCE_LITE_I2V = "ep-20260331152207-2n5zd"  # 1.0 Lite i2v，支持 reference_image(1~4张)
CALLBACK_URL = "https://shop-ai.xin/api/v1/video-thread/callback"

MAX_PROMPT_CHARS_ZH = 500
MAX_PROMPT_WORDS_EN = 1000


def _validate_prompt(prompt: str) -> str:
    """校验提示词长度，超限则截断并记录警告。官方限制：中文≤500字/英文≤1000词。"""
    if not prompt:
        raise ValueError("segment description 不能为空")
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", prompt))
    if zh_chars > len(prompt) * 0.3:
        if len(prompt) > MAX_PROMPT_CHARS_ZH:
            logger.warning("中文提示词超过 %d 字，已截断", MAX_PROMPT_CHARS_ZH)
            prompt = prompt[:MAX_PROMPT_CHARS_ZH]
    else:
        words = prompt.split()
        if len(words) > MAX_PROMPT_WORDS_EN:
            logger.warning("英文提示词超过 %d 词，已截断", MAX_PROMPT_WORDS_EN)
            prompt = " ".join(words[:MAX_PROMPT_WORDS_EN])
    return prompt


def build_payload_for_segment(
    segment: dict,
    config: dict,
) -> dict[str, Any]:
    """
    根据单个剧本片段构建 Seedance 视频生成 API 的请求 payload。

    segment.mode 与 Seedance 能力映射:
      - text_to_video:        纯文生视频 (1.5 pro)
      - image_to_video:       参考图生视频 (1.0 Lite i2v, reference_image, 1~4张)
      - frame_interpolation:  图生视频-首尾帧 (1.5 pro, first_frame + last_frame)
    """
    mode = segment.get("mode", "text_to_video")
    prompt = segment.get("description", "")
    prompt = _validate_prompt(prompt)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

    if mode == "text_to_video":
        model = MODEL_SEEDANCE_15_PRO

    elif mode == "image_to_video":
        image_urls = [u for u in segment.get("image_urls", []) if u]
        if image_urls:
            model = MODEL_SEEDANCE_LITE_I2V
            for url in image_urls[:4]:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": "reference_image",
                })
        else:
            model = MODEL_SEEDANCE_15_PRO

    elif mode == "frame_interpolation":
        model = MODEL_SEEDANCE_15_PRO
        first = segment.get("first_frame_url", "")
        last = segment.get("last_frame_url", "")
        if first:
            content.append({
                "type": "image_url",
                "image_url": {"url": first},
                "role": "first_frame",
            })
        if last:
            content.append({
                "type": "image_url",
                "image_url": {"url": last},
                "role": "last_frame",
            })

    else:
        model = MODEL_SEEDANCE_15_PRO

    payload: dict[str, Any] = {
        "model": model,
        "content": content,
        "callback_url": CALLBACK_URL,
    }

    duration = segment.get("duration")
    if duration is not None:
        payload["duration"] = max(4, min(duration, 12))

    ratio = config.get("ratio")
    if ratio:
        payload["ratio"] = ratio

    resolution = config.get("resolution")
    if resolution:
        payload["resolution"] = resolution

    watermark = config.get("watermark")
    if watermark is not None:
        payload["watermark"] = watermark

    generate_audio = config.get("generate_audio")
    if generate_audio is not None:
        payload["generate_audio"] = generate_audio

    payload["return_last_frame"] = True

    return payload

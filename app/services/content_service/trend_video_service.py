"""
热点 × 品牌 × 产品 → 病毒式短视频：Seedance payload 组装服务。
将前端请求参数转化为 Seedance API（火山引擎方舟）可直接消费的请求体。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from app.core.config import get_settings
from app.schemas.content import TrendProductVideoRequest
from app.services.content_service.prompt_templates import build_trend_product_video_prompt

logger = logging.getLogger(__name__)

_MODEL_TEXT_VIDEO = "ep-20260330165459-vmz9x"
_MODEL_REF_VIDEO = "ep-20260331152207-2n5zd"


def _get_llm_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.LLM_API_KEY:
        raise ValueError("未配置 LLM_API_KEY，无法优化 Prompt")
    return AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_API_URL,
        timeout=60,
        max_retries=2,
        http_client=httpx.AsyncClient(proxy=None),
    )


async def _optimize_prompt_via_llm(raw_prompt: str) -> str:
    """
    将模板拼接出的 meta-prompt 发送给 Qwen，让 LLM 输出
    一段可直接喂给 Seedance 的英文视频画面描述。
    LLM 调用失败时自动降级为原始 Prompt，不阻塞主流程。
    """
    settings = get_settings()
    try:
        client = _get_llm_client()
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": raw_prompt}],
            temperature=0.7,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        if not content:
            logger.warning("LLM 返回空内容，回退使用原始 Prompt")
            return raw_prompt
        logger.info("LLM Prompt 优化成功，长度 %d -> %d", len(raw_prompt), len(content))
        return content.strip()
    except (APIConnectionError, APITimeoutError) as e:
        logger.warning("LLM 连接/超时失败，回退使用原始 Prompt: %s", e)
        return raw_prompt
    except Exception as e:
        logger.warning("LLM 调用异常，回退使用原始 Prompt: %s", e)
        return raw_prompt


def _build_text_content(prompt: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": prompt}]


def _build_image_content(
    prompt: str,
    image_urls: List[str],
    role_mode: str,
) -> List[Dict[str, Any]]:
    """
    role_mode:
      - "i2v"  : 图生视频 — 1 张图无 role，2 张图分别为 first_frame / last_frame
      - "ref"  : 参考图生视频 — 每张图的 role 均为 reference_image
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

    if role_mode == "ref":
        for url in image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            })
    else:
        if len(image_urls) == 1:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_urls[0]},
            })
        elif len(image_urls) >= 2:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_urls[0]},
                "role": "first_frame",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": image_urls[1]},
                "role": "last_frame",
            })

    return content


async def build_seedance_payload(
    req: TrendProductVideoRequest,
) -> tuple[Dict[str, Any], str]:
    """
    将 TrendProductVideoRequest 转化为 Seedance 视频生成 API 的完整请求体。
    返回 (payload_dict, prompt_used)。
    """
    raw_prompt = build_trend_product_video_prompt(
        trend=req.trendObject,
        brand=req.brandObject,
        product=req.productObject,
        user_prompt=req.user_prompt,
    )

    prompt = await _optimize_prompt_via_llm(raw_prompt)

    gen_type = req.generation_type
    image_urls = req.image_urls or []

    if gen_type == "text_to_video":
        content = _build_text_content(prompt)
        model = _MODEL_TEXT_VIDEO
    elif gen_type == "image_to_video":
        content = _build_image_content(prompt, image_urls, role_mode="i2v")
        model = _MODEL_TEXT_VIDEO
    elif gen_type == "ref_to_video":
        content = _build_image_content(prompt, image_urls, role_mode="ref")
        model = _MODEL_REF_VIDEO
    else:
        content = _build_text_content(prompt)
        model = _MODEL_TEXT_VIDEO

    payload: Dict[str, Any] = {
        "model": model,
        "content": content,
    }

    if req.duration is not None:
        payload["duration"] = req.duration
    if req.ratio is not None:
        payload["ratio"] = req.ratio
    if req.watermark is not None:
        payload["watermark"] = req.watermark
    if req.generate_audio is not None:
        payload["generate_audio"] = req.generate_audio

    return payload, prompt

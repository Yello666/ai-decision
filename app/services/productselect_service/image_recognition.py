"""用 qwen-vl-plus（DashScope）对抽帧图片做电商选品视觉识别。

将同一视频的多帧合并送入一次请求，让模型在更多上下文下识别画面中
「适合做电商商品的实物物件」（服饰、鞋帽、首饰配饰、包袋、手表、玩具手办、周边等）。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from . import config

logger = logging.getLogger(__name__)

_PROMPT = """你是电商选品视觉分析师，专长是发现「与名人或知名 IP 相关、可做成电商商品的物件」。
下面是来自同一条视频的若干帧画面，请综合这些帧进行分析。

【核心任务】优先识别那些与名人、明星、动漫/游戏/影视 IP、知名品牌或角色相关联的实物物件，例如：
- 名人/明星的同款穿搭、配饰、鞋帽、包袋、手表、首饰（耳环、项链、戒指、手链）；
- 动漫/游戏/影视角色的周边谷子、手办、立牌、徽章、cosplay 配件、角色标志性物件；
- 带有知名 IP / 品牌形象或 logo 的商品。

【判定要求】
1. 每张图片在它前面都给出了对应的文件名，请记住每个物件分别出现在哪一张/哪几张图片里。
2. 尽量结合画面中的人物、角色、logo、标志性元素，结合网络搜索相似图，推断其关联的具体名人或 IP 名称。
3. 只要能合理判断出关联的名人/IP，就在 reason 中明确写出该名人或 IP 的名字（如「与 MrBeast 联名」「初音未来同款」「与角色 XX 强绑定」）。
4. 若实在无法判断具体名人/IP，可保留物件但在 related_ip 填 "未知"，reason 说明无法确认关联对象。
5. 忽略：纯背景风景、文字字幕、无法作为商品的抽象内容。

严格输出 JSON（不要 markdown 代码块、不要多余解释），结构：
{
  "objects": [ 
    {
      "category": "品类，如 耳环/卫衣/手办",
      "related_ip": "关联的名人或 IP 名称；无法确认填 未知",
      "source_images": ["该物件出现的图片文件名，必须从给定文件名中原样照抄，可多张"],
      "description": "外观简述（颜色/材质/造型特征）",
      "attributes": ["关键特征标签", "..."],
      "ecommerce_potential": "high | medium | low",
      "reason": "一两句话理由，尽量包含关联的名人/IP 名字"
    }
  ]
}
若画面中没有与名人/IP 相关、可作为商品的物件，objects 返回空数组 []。"""

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client
    api_key = config.get_vl_api_key()
    if not api_key:
        raise RuntimeError(
            "未找到 API Key：请在项目根 .env 配置 LLM_API_KEY（DashScope），"
            "或设置环境变量 DASHSCOPE_API_KEY。"
        )
    # proxy=None：与项目其它 LLM 客户端一致，避免系统代理干扰国内接口
    _client = OpenAI(
        api_key=api_key,
        base_url=config.VL_BASE_URL,
        http_client=httpx.Client(proxy=None),
    )
    return _client


def _to_data_url(image_path: Path) -> str:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _parse_json(text: str) -> dict[str, Any]:
    """从模型输出里尽力解析 JSON（兼容可能出现的 ```json 代码块）。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # 去掉可能的语言标注行，如 json\n{...}
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                cleaned = rest
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            data.setdefault("objects", [])
            return data
    except json.JSONDecodeError:
        logger.warning("识图结果 JSON 解析失败，原始输出片段：%s", cleaned[:300])
    return {"objects": [], "raw_text": cleaned}


def recognize_images(
    image_paths: list[Path],
    known_ip: str | None = None,
) -> dict[str, Any]:
    """对一组图片识别，返回结构化物件清单。

    known_ip：若已知这些图片来自某名人/IP（如 Instagram 账号），传入后会提示模型
    优先围绕该对象判断关联与同款商品，并把它作为 related_ip 的默认参考。
    """
    if not image_paths:
        return {"objects": []}

    picked = image_paths[: config.VL_MAX_IMAGES_PER_REQUEST]
    content: list[dict[str, Any]] = [{"type": "text", "text": _PROMPT}]
    if known_ip:
        content.append(
            {
                "type": "text",
                "text": (
                    f"补充背景：以下图片主要来自「{known_ip}」的账号/频道。\n"
                    f"1) 可优先留意与「{known_ip}」本人相关的同款、配饰与周边；\n"
                    f"2) 但不要只盯着「{known_ip}」的身份——画面里其它有电商价值的物件"
                    f"（不论是否属于此人）同样要识别出来，不要漏掉；\n"
                    f"3) related_ip 只有在你确信该物件确实与「{known_ip}」强相关时才填「{known_ip}」；"
                    f"若属于其它名人/品牌/IP 就如实填写，无法确认则填「未知」，"
                    f"不要为了凑关联而硬安到「{known_ip}」头上。"
                ),
            }
        )
    for idx, path in enumerate(picked, start=1):
        # 在每张图前标注其文件名，便于模型在 source_images 中按名回填
        content.append({"type": "text", "text": f"第{idx}张图片，文件名：{path.name}"})
        content.append({"type": "image_url", "image_url": {"url": _to_data_url(path)}})

    client = _get_client()
    response = client.chat.completions.create(
        model=config.VL_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
    )
    text = response.choices[0].message.content if response.choices else ""
    result = _parse_json(text or "")
    result["frame_count"] = len(picked)
    result["images"] = [p.name for p in picked]
    result["token_usage"] = _extract_usage(response)
    if known_ip:
        result["known_ip"] = known_ip
    return result


def _extract_usage(response: Any) -> dict[str, Any]:
    """从模型响应里提取 token 用量。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "model": config.VL_MODEL,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }

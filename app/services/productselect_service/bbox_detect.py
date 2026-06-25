"""用 qwen-vl-plus 对单张图检测「可做电商的商品物件」并输出归一化坐标框（bbox）。

坐标用归一化 0~1（相对图片宽高），下游用 Pillow 还原成像素再裁剪。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from . import config
from .image_recognition import _get_client

logger = logging.getLogger(__name__)

_BBOX_PROMPT = """你是电商选品视觉分析师。请在这张图片中找出【适合做成电商商品的实物物件】
（服饰、鞋帽、首饰/配饰、包袋、手表、玩具/手办、IP 周边、3C 小物等），
并给出每个物件的边界框坐标，用于后续裁剪。

坐标要求：使用归一化坐标，x、y 均为 0~1 的小数（相对图片宽度/高度），
box = [x1, y1, x2, y2]，其中 (x1,y1) 为左上角、(x2,y2) 为右下角。
框要尽量贴合该物件本身（不要框住整张图或大片背景）。

严格输出 JSON（不要 markdown、不要多余解释）：
{
  "objects": [
    {
      "category": "品类，如 球衣/球鞋/耳机",
      "related_ip": "关联名人或IP；无法确认填 未知",
      "ecommerce_potential": "high | medium | low",
      "box": [x1, y1, x2, y2]
    }
  ]
}
没有可做商品的物件则 objects 返回 []。"""


def _to_data_url(image_path: Path) -> str:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
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
        logger.warning("bbox 结果 JSON 解析失败，片段：%s", cleaned[:300])
    return {"objects": [], "raw_text": cleaned}


def _normalize_box(box: Any) -> list[float] | None:
    """校验并裁剪 box 到合法的归一化 [x1,y1,x2,y2]（0~1，且 x1<x2,y1<y2）。"""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    # 个别模型可能给 0~1000 或像素，>1 时按 0~1000 归一处理（粗略兜底）
    if max(x1, y1, x2, y2) > 1.0:
        x1, y1, x2, y2 = (v / 1000.0 for v in (x1, y1, x2, y2))
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 - x1 < 0.01 or y2 - y1 < 0.01:
        return None
    return [x1, y1, x2, y2]


def detect_objects_with_boxes(image_path: Path, known_ip: str | None = None) -> list[dict[str, Any]]:
    """返回 [{category, related_ip, ecommerce_potential, box:[x1,y1,x2,y2](归一化)}]。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": _BBOX_PROMPT}]
    if known_ip:
        content.append(
            {"type": "text", "text": f"补充背景：图片来自「{known_ip}」，related_ip 确实属于此人才填该名字。"}
        )
    content.append({"type": "image_url", "image_url": {"url": _to_data_url(image_path)}})

    client = _get_client()
    response = client.chat.completions.create(
        model=config.VL_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
    )
    text = response.choices[0].message.content if response.choices else ""
    parsed = _parse_json(text or "")

    objects: list[dict[str, Any]] = []
    for obj in parsed.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        box = _normalize_box(obj.get("box"))
        if box is None:
            logger.info("跳过无有效 box 的物件：%s", obj.get("category"))
            continue
        objects.append(
            {
                "category": obj.get("category") or "",
                "related_ip": obj.get("related_ip") or "",
                "ecommerce_potential": (obj.get("ecommerce_potential") or "").strip().lower(),
                "box": box,
            }
        )
    logger.info("检测到 %d 个带 box 的物件（图：%s）", len(objects), image_path.name)
    return objects

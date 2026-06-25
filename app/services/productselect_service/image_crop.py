"""按归一化 bbox 用 Pillow 裁剪出单个商品小图。"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from . import config

logger = logging.getLogger(__name__)


def crop_by_norm_box(
    image_path: Path,
    box: list[float],
    out_path: Path,
    padding_ratio: float | None = None,
) -> Path | None:
    """把归一化 box=[x1,y1,x2,y2]（0~1）还原为像素并裁剪，保存到 out_path。

    会在框四周按 padding_ratio 留一点边距，识别更稳。失败返回 None。
    """
    pad = config.CROP_PADDING_RATIO if padding_ratio is None else padding_ratio
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        logger.exception("打开图片失败：%s", image_path)
        return None

    w, h = img.size
    x1, y1, x2, y2 = box
    # 加边距
    bw, bh = (x2 - x1), (y2 - y1)
    x1 -= bw * pad
    x2 += bw * pad
    y1 -= bh * pad
    y2 += bh * pad
    # 还原像素并裁剪到图像范围内
    left = max(0, int(x1 * w))
    top = max(0, int(y1 * h))
    right = min(w, int(x2 * w))
    bottom = min(h, int(y2 * h))
    if right - left < 2 or bottom - top < 2:
        logger.warning("裁剪区域过小，跳过：%s box=%s", image_path.name, box)
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.crop((left, top, right, bottom)).save(out_path, "JPEG", quality=90)
    except Exception:
        logger.exception("裁剪保存失败：%s", out_path)
        return None
    logger.info("已裁剪 %s → %s", image_path.name, out_path.name)
    return out_path

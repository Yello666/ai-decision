"""对 youtube_pic 下【已抽好的图片】做 qwen-vl-plus 识图（无需重新下载视频）。

适合你已经跑过 run_capture、本地已有图片的场景：
扫描 config.OUTPUT_DIR 下每个「视频目录」，把该目录里的帧合并送入模型，
结果写到该目录的 recognition.json。

运行方式（在项目根目录 D:\\ai-decision 下执行）：
       python -m app.services.productselect_service.run_recognize
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config
from .image_recognition import recognize_images

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _iter_video_dirs(root: Path):
    """产出包含图片的目录（每个视频一个目录）。"""
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        frames = sorted(p for p in path.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)
        if frames:
            yield path, frames


def run() -> None:
    _setup_logging()
    logger = logging.getLogger("productselect")

    root = config.OUTPUT_DIR
    if not root.exists():
        logger.warning("图片目录不存在：%s（请先运行 run_capture 抽帧）", root)
        return

    logger.info("开始识图，扫描目录：%s", root)
    dir_count = 0
    object_total = 0
    for video_dir, frames in _iter_video_dirs(root):
        dir_count += 1
        try:
            result = recognize_images(frames)
        except Exception:
            logger.exception("识图失败：%s", video_dir)
            continue
        result["video_id"] = video_dir.name
        out_path = video_dir / "recognition.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        objects = result.get("objects", [])
        object_total += len(objects)
        logger.info("识图完成 %s 物件数=%d → %s", video_dir.name, len(objects), out_path)

    logger.info("全部完成：处理 %d 个视频目录，累计识别物件 %d 个", dir_count, object_total)


if __name__ == "__main__":
    run()

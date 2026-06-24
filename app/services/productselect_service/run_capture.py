"""选频道 → 抽帧取图 →（可选）qwen-vl-plus 识图 的编排入口。

运行前准备：
  1) 激活虚拟环境后安装 yt-dlp：
       .\\.venv\\Scripts\\Activate.ps1
       pip install yt-dlp
  2) 安装 ffmpeg（Windows 可用：winget install Gyan.FFmpeg，或下载后把 bin 加入 PATH）；
     若不想改 PATH，也可在 config.py 的 FFMPEG_BIN 填 ffmpeg.exe 的绝对路径。
  3) 在 config.py 里填写要监控的频道 CHANNELS、抽帧数量等。
     识图复用项目 .env 里的 LLM_API_KEY（DashScope），可用 ENABLE_RECOGNITION_AFTER_CAPTURE 开关。

运行方式（在项目根目录 D:\\ai-decision 下执行）：
       python -m app.services.productselect_service.run_capture

图片保存到 config.OUTPUT_DIR（默认 D:\\ai-decision\\productSelect\\youtube_pic）下，
按 频道/视频ID/ 分目录；开启识图后，每个视频目录会额外生成 recognition.json。
已处理过的视频（已存在 recognition.json，或仅抽帧模式下已有图片）会自动跳过，避免重复花钱。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config
from .frame_extractor import capture_frames
from .image_recognition import recognize_images
from .youtube_channel import list_channel_videos


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _already_processed(out_dir: Path) -> bool:
    """去重：判断该视频是否已处理过，避免重复抽帧与重复识图。

    - 开启识图时：以 recognition.json 是否存在为准；
    - 仅抽帧时：以目录中是否已有抽好的图片为准。
    """
    if config.ENABLE_RECOGNITION_AFTER_CAPTURE:
        return (out_dir / "recognition.json").exists()
    return out_dir.exists() and any(out_dir.glob("*.jpg"))


def run() -> None:
    _setup_logging()
    logger = logging.getLogger("productselect")
    logger.info("开始抽帧任务，输出目录：%s", config.OUTPUT_DIR)

    total_frames = 0
    for channel in config.CHANNELS:
        try:
            videos = list_channel_videos(channel, config.MAX_VIDEOS_PER_CHANNEL)
        except Exception:
            logger.exception("获取频道视频失败：%s", channel)
            continue

        safe_channel = channel.strip().lstrip("@").replace("/", "_").replace(":", "_")
        for video in videos:
            out_dir = config.OUTPUT_DIR / safe_channel / video.video_id
            if _already_processed(out_dir):
                logger.info("跳过已处理视频 video=%s（%s）", video.video_id, video.title)
                continue
            try:
                frames = capture_frames(video.video_id, video.url, out_dir)
            except Exception:
                logger.exception("抽帧失败 video=%s（%s）", video.video_id, video.title)
                continue
            total_frames += len(frames)
            logger.info("视频《%s》(%s) 抽帧 %d 张", video.title, video.video_id, len(frames))

            if config.ENABLE_RECOGNITION_AFTER_CAPTURE and frames:
                try:
                    result = recognize_images(frames, known_ip=config.display_name(channel))
                    result["video_id"] = video.video_id
                    result["title"] = video.title
                    out_path = out_dir / "recognition.json"
                    out_path.write_text(
                        json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info(
                        "识图完成 video=%s 物件数=%d → %s",
                        video.video_id, len(result.get("objects", [])), out_path,
                    )
                except Exception:
                    logger.exception("识图失败 video=%s（%s）", video.video_id, video.title)

    logger.info("全部完成，共保存 %d 张图片到 %s", total_frames, config.OUTPUT_DIR)


if __name__ == "__main__":
    run()

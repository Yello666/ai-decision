"""解析视频直链并用 ffmpeg 稀疏抽帧保存为图片（不下载整段视频）。

原理：
  - yt-dlp 只负责解析出带签名的流媒体直链（不落盘视频）。
  - ffmpeg 用「输入定位」(-ss 放在 -i 前) + HTTP Range 请求，
    每帧只拉取所需的一小段字节，因此单帧通常秒级、几乎不占磁盘。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import yt_dlp

from . import config

logger = logging.getLogger(__name__)


def _resolve_stream(video_url: str) -> tuple[str, float | None]:
    """解析视频的直链与时长。返回 (stream_url, duration_seconds)。"""
    ydl_opts = {
        # 优先取不超过 MAX_HEIGHT 的合流格式，省流量也便于 ffmpeg 直接读
        "format": f"best[height<={config.MAX_HEIGHT}]/best",
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    stream_url = (info or {}).get("url")
    duration = (info or {}).get("duration")
    if not stream_url:
        raise RuntimeError(f"无法解析视频直链: {video_url}")
    return stream_url, duration


def _build_timestamps(duration: float | None) -> list[int]:
    """按配置生成抽帧时间点列表。"""
    start = config.START_OFFSET_SECONDS
    step = max(1, config.FRAME_INTERVAL_SECONDS)
    max_frames = max(1, config.MAX_FRAMES_PER_VIDEO)

    timestamps: list[int] = []
    t = start
    while len(timestamps) < max_frames:
        if duration and t >= duration:
            break
        timestamps.append(int(t))
        t += step

    if not timestamps:
        timestamps = [start]
    return timestamps


def capture_frames(video_id: str, video_url: str, out_dir: Path) -> list[Path]:
    """对单条视频稀疏抽帧并保存到 out_dir，返回已保存的图片路径列表。"""
    stream_url, duration = _resolve_stream(video_url)
    timestamps = _build_timestamps(duration)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for idx, ts in enumerate(timestamps, start=1):
        out_path = out_dir / f"{video_id}_{idx:03d}_{ts}s.jpg"
        cmd = [
            config.FFMPEG_BIN,
            "-ss", str(ts),     # 输入定位：放在 -i 前，只拉取该时间点附近字节
            "-i", stream_url,
            "-frames:v", "1",   # 只取 1 帧
            "-q:v", "2",        # JPEG 质量（数值越小越清晰）
            "-y",               # 覆盖同名文件
            str(out_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=config.FRAME_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "找不到 ffmpeg。请先安装 ffmpeg 并加入 PATH，"
                "或在 config.py 的 FFMPEG_BIN 填写绝对路径。"
            ) from exc
        except subprocess.TimeoutExpired:
            logger.warning("抽帧超时，跳过 video=%s ts=%ss", video_id, ts)
            continue

        if result.returncode == 0 and out_path.exists():
            saved.append(out_path)
            logger.info("已保存帧 %s", out_path)
        else:
            stderr_tail = (result.stderr or b"").decode("utf-8", "ignore")[-300:]
            logger.warning(
                "抽帧失败 video=%s ts=%ss returncode=%s stderr=%s",
                video_id, ts, result.returncode, stderr_tail,
            )

    return saved

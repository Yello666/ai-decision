"""按频道获取最新视频列表（基于 yt-dlp，无需 YouTube Data API key/配额）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel: str


def _normalize_channel_url(channel: str) -> str:
    """把 @handle / 频道ID / 完整URL 统一规整成频道的 /videos 列表页地址。"""
    c = channel.strip()
    if c.startswith(("http://", "https://")):
        base = c.rstrip("/")
    elif c.startswith("@"):
        base = f"https://www.youtube.com/{c}"
    elif c.startswith("UC") and len(c) >= 20:
        base = f"https://www.youtube.com/channel/{c}"
    else:
        base = f"https://www.youtube.com/@{c}"
    if not base.endswith("/videos"):
        base = f"{base}/videos"
    return base


def list_channel_videos(channel: str, max_videos: int) -> list[VideoInfo]:
    """拉取某频道最新的 max_videos 条视频（仅元数据，不下载）。"""
    url = _normalize_channel_url(channel)
    ydl_opts = {
        # flat 模式：只取播放列表里的条目元数据，不进每个视频详情，速度快
        "extract_flat": "in_playlist",
        "quiet": True,
        "skip_download": True,
        "playlistend": max_videos,
        "ignoreerrors": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = (info or {}).get("entries") or []
    videos: list[VideoInfo] = []
    for entry in entries[:max_videos]:
        if not entry:
            continue
        vid = entry.get("id")
        if not vid:
            continue
        videos.append(
            VideoInfo(
                video_id=vid,
                title=entry.get("title") or vid,
                # flat 条目里的 url 形态不稳定，统一用 video_id 拼标准 watch 链接
                url=f"https://www.youtube.com/watch?v={vid}",
                channel=channel,
            )
        )

    logger.info("频道 %s 获取到 %d 条视频", channel, len(videos))
    return videos

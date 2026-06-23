"""抽帧任务配置：直接改这里即可，无需改其它文件。"""

from __future__ import annotations

from pathlib import Path

# 项目根目录：当前文件位于 app/services/productselect_service/，向上三级即 ai-decision/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 抽帧图片输出目录（按需求保存到 D:\ai-decision\youtube_pic）
OUTPUT_DIR = PROJECT_ROOT / "youtube_pic"

# 要监控的频道列表，支持三种写法：
#   - "@MrBeast"                       （频道 handle）
#   - "UCX6OQ3DkcsbYNE6H8uQQuVA"        （频道 ID，UC 开头）
#   - "https://www.youtube.com/@MrBeast"（完整频道 URL）
CHANNELS: list[str] = [
    "@MrBeast",
]

# 每个频道最多取多少条最新视频
MAX_VIDEOS_PER_CHANNEL = 3

# 每条视频最多抽多少帧
MAX_FRAMES_PER_VIDEO = 5

# 抽帧策略：从 START_OFFSET_SECONDS 开始，每隔 FRAME_INTERVAL_SECONDS 抽一帧
START_OFFSET_SECONDS = 5
FRAME_INTERVAL_SECONDS = 10

# 解析直链时的画质上限（高度像素）；越小下载越快，识图前期 480 足够
MAX_HEIGHT = 1080

# ffmpeg 可执行文件；已加入系统 PATH 时保持 "ffmpeg" 即可，
# 否则填绝对路径，如 r"C:\\ffmpeg\\bin\\ffmpeg.exe"
FFMPEG_BIN = "D:\\Tools\\ffmpeg-8.1.1-full_build\\ffmpeg-8.1.1-full_build\\bin\\ffmpeg.exe"

# 单帧抽取的超时时间（秒），防止个别视频卡死
FRAME_TIMEOUT_SECONDS = 120

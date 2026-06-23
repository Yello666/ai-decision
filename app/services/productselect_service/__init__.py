"""
选品服务（productselect_service）

当前阶段只做「选好频道 → 抽帧取图」，不做图片识别：
  1. youtube_channel.py —— 按频道拉取最新视频列表（基于 yt-dlp，无需 YouTube API key）
  2. frame_extractor.py —— 解析视频直链并用 ffmpeg 稀疏抽帧，保存为图片（不下载整段视频）
  3. run_capture.py     —— 编排入口，可直接运行

运行方式见 run_capture.py 顶部说明。
"""

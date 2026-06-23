"""
选品服务（productselect_service）

两条来源管线，最终都走「取图 → qwen-vl-plus 识图 → 名人/IP 关联商品机会」：

YouTube（按频道抽帧）:
  1. youtube_channel.py  —— 按频道拉取最新视频列表（基于 yt-dlp，无需 YouTube API key）
  2. frame_extractor.py  —— 解析视频直链并用 ffmpeg 稀疏抽帧（不下载整段视频）
  3. run_capture.py      —— 抽帧（可选顺带识图）入口
  4. run_recognize.py    —— 对已抽好的图片单独识图

Instagram（按账号抓帖）:
  5. instagram_apify.py  —— 用 Apify 抓某名人账号最新帖子的图片（复用 .env 的 APIFY_API_KEY）
  6. run_instagram.py    —— 监控池 → 抓帖 → 下载图片 → 识图 入口

公共:
  - config.py            —— 所有可调参数（频道/监控池/抽帧/识图模型等）
  - image_recognition.py —— qwen-vl-plus 识图，支持 known_ip 提示

各入口运行方式见对应文件顶部说明。
"""

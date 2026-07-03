"""抽帧任务配置：直接改这里即可，无需改其它文件。"""

from __future__ import annotations

from pathlib import Path

# 项目根目录：当前文件位于 app/services/productselect_service/，向上三级即 ai-decision/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 选品产物统一根目录（便于管理）：D:\ai-decision\productSelect\
PRODUCT_SELECT_DIR = PROJECT_ROOT / "productSelect"

# YouTube 抽帧图片输出目录
OUTPUT_DIR = PRODUCT_SELECT_DIR / "youtube_pic"

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

# ------------------------------
# 识图（DashScope 上的通义千问视觉模型）
# ------------------------------
# 视觉模型名称
VL_MODEL = "qwen3.7-plus"

# DashScope OpenAI 兼容接口（与项目主配置 LLM_API_URL 一致）
VL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 单次请求最多携带多少张图片（同一视频的多帧合并送入，给模型更多上下文）
VL_MAX_IMAGES_PER_REQUEST = 5
# 抽帧结束后是否自动对该视频的帧做识图（False 时只抽帧，可后续用 run_recognize.py 单独识图）
ENABLE_RECOGNITION_AFTER_CAPTURE = True


# ------------------------------
# 账号/频道 handle → 真人或 IP 的可读名称
# ------------------------------
# 识图时把可读名称注入提示词锚定（handle 往往很隐晦，真名锚定更准）。
# 找不到映射时回退使用原始 handle。键统一用小写、去掉 @。
IP_DISPLAY_NAMES: dict[str, str] = {
    "csgoniko": "NiKo（CS 电竞选手 Nikola Kovač）",
    "cristiano": "C 罗（Cristiano Ronaldo）",
    "kyliejenner": "Kylie Jenner",
    "kimkardashian": "Kim Kardashian",
    "kendalljenner": "Kendall Jenner",
    "badgalriri": "Rihanna",
    "selenagomez": "Selena Gomez",
    "zendaya": "Zendaya",
    "leomessi": "梅西（Lionel Messi）",
    "taylorswift": "Taylor Swift",
    "virat.kohli": "Virat Kohli",
    "mrbeast": "MrBeast（野兽先生）",
}


def display_name(account: str) -> str:
    """把账号/频道 handle 映射为可读名称；无映射则回退去掉 @ 的原始 handle。"""
    key = (account or "").strip().lstrip("@").lower()
    return IP_DISPLAY_NAMES.get(key, (account or "").strip().lstrip("@"))


# ------------------------------
# Instagram 名人监控（Apify）
# ------------------------------
# 监控池：要监控的 Instagram 名人账号（用户名，含或不含 @ 均可）
# 默认选了一批时尚/配饰/周边内容丰富、适合电商选品的高影响力账号，可按需替换
INSTAGRAM_PROFILES: list[str] = [
    "csgoniko",       # NiKo / Nikola Kovač（CS 电竞选手，置顶监控）
    "cristiano",      # Cristiano Ronaldo（运动/穿搭/配饰）
    "kyliejenner",    # Kylie Jenner（美妆/时尚）
    "kimkardashian",  # Kim Kardashian（时尚/配饰）
    "kendalljenner",  # Kendall Jenner（时尚/穿搭）
    "badgalriri",     # Rihanna（Fenty 时尚美妆）
    "selenagomez",    # Selena Gomez（美妆/穿搭）
    "zendaya",        # Zendaya（红毯/时尚）
    "leomessi",       # Lionel Messi（运动/穿搭）
    "taylorswift",    # Taylor Swift（周边/穿搭）
    "virat.kohli",    # Virat Kohli（运动/配饰）
]
# 每个账号抓取最新多少条帖子
INSTAGRAM_POSTS_PER_PROFILE = 5
# 每条帖子最多取多少张图片（轮播帖会有多图）送去识别
INSTAGRAM_MAX_IMAGES_PER_POST = 4
# Instagram 图片下载与识别结果的输出目录
INSTAGRAM_OUTPUT_DIR = PRODUCT_SELECT_DIR / "instagram_pic"
# Apify 上的 Instagram 抓取 Actor（可用 "用户名/actor名" 或 actor id）
APIFY_INSTAGRAM_ACTOR = "apify/instagram-scraper"


def _read_env(*names: str) -> str | None:
    """按顺序从环境变量、再从项目根 .env 读取第一个非空值。"""
    import os

    for name in names:
        value = os.getenv(name)
        if value:
            return value
    try:
        from dotenv import dotenv_values

        values = dotenv_values(PROJECT_ROOT / ".env")
        for name in names:
            value = values.get(name)
            if value:
                return value
    except Exception:
        return None
    return None


def get_vl_api_key() -> str | None:
    """读取视觉模型 API Key（复用项目 DashScope 的 LLM_API_KEY，无需新增密钥）。"""
    return _read_env("DASHSCOPE_API_KEY", "LLM_API_KEY")


def get_apify_api_key() -> str | None:
    """读取 Apify API Key（复用项目 .env 里的 APIFY_API_KEY）。"""
    return _read_env("APIFY_API_KEY")


def get_serpapi_api_key() -> str | None:
    """读取 SerpApi API Key（复用项目 .env 里的 SERPAPI_API_KEY）。"""
    return _read_env("SERPAPI_API_KEY")


def get_oss_config() -> dict[str, str | None]:
    """读取阿里云 OSS 配置（复用 .env 的 AK/SK；Endpoint/Bucket 缺省同主项目）。"""
    return {
        "ak": _read_env("AK"),
        "sk": _read_env("SK"),
        "endpoint": _read_env("Endpoint") or OSS_ENDPOINT,
        "bucket": _read_env("Bucket") or OSS_BUCKET,
        "region": OSS_REGION,
    }


# ------------------------------
# 供应链对齐（裁剪 → OSS → SerpApi Google Lens）
# ------------------------------
# 裁剪图本地输出目录
CROP_OUTPUT_DIR = PRODUCT_SELECT_DIR / "crops"
# 供应链测试结果输出目录
SUPPLY_TEST_DIR = PRODUCT_SELECT_DIR / "supply_test"

# 裁剪时在 bbox 四周留的边距比例（0~0.2），稍微留点边识别更稳
CROP_PADDING_RATIO = 0.04

# OSS（私有 Bucket，上传后用签名 URL 给 SerpApi 抓取）
OSS_ENDPOINT = "oss-ap-southeast-1.aliyuncs.com"
OSS_BUCKET = "video-upload-shopai"
OSS_REGION = "ap-southeast-1"
OSS_SOURCE_PREFIX = "productselect/sources/"
OSS_CROP_PREFIX = "productselect/crops/"
OSS_RECOGNITION_PREFIX = "productselect/recognition/"
# 兼容旧代码引用
OSS_UPLOAD_PREFIX = OSS_CROP_PREFIX
OSS_SIGN_URL_EXPIRE = 3600  # Lens 等即时调用
OSS_API_SIGN_URL_EXPIRE = 21600  # API 返回给前端的签名有效期（6 小时）

# SerpApi Google Lens
# type: all（最全，含 visual_matches + related_content）| products | visual_matches | exact_matches
SERPAPI_LENS_TYPE = "products"
SERPAPI_LENS_COUNTRY = "us"

# 只对这些潜力等级的物件调用 SerpApi（控制搜索次数与成本）。
# 例：["high"] 只查高潜力；["high","medium"] 查高+中；置为 [] 或 None 表示不过滤、全部查。
SUPPLY_POTENTIAL_FILTER: list[str] | None = ["high"]

# 供应链测试入口要处理的图片（相对项目根或绝对路径）
SUPPLY_TEST_IMAGES: list[str] = [
    "productSelect/instagram_pic/cristiano/3926051279432646990/3926051279432646990_01.jpg",
    "productSelect/instagram_pic/csgoniko/3920104545275358222/3920104545275358222_01.jpg",
]

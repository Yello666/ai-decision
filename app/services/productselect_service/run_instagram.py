"""监控池里的 Instagram 名人 → Apify 抓最新帖子 → 下载图片 → qwen-vl-plus 识图。

与 YouTube 抽帧识图同构，区别只是来源换成 Instagram 帖子图片。

运行前准备：
  1) 激活虚拟环境并安装依赖：
       .\\.venv\\Scripts\\Activate.ps1
       pip install apify-client httpx
     （识图复用项目 .env 里的 LLM_API_KEY；抓取复用 APIFY_API_KEY）
  2) 在 config.py 的 INSTAGRAM_PROFILES 里填写要监控的名人账号（监控池）。

运行方式（在项目根目录 D:\\ai-decision 下执行）：
       python -m app.services.productselect_service.run_instagram

输出：图片与结果存到 config.INSTAGRAM_OUTPUT_DIR（默认 D:\\ai-decision\\instagram_pic）下，
按 账号/帖子ID/ 分目录，每个帖子目录含图片与 recognition.json。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from . import config
from .image_recognition import recognize_images
from .instagram_apify import InstagramPost, fetch_latest_posts


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


logger = logging.getLogger("productselect.instagram")


def _download_images(post: InstagramPost, out_dir: Path) -> list[Path]:
    """下载帖子图片到 out_dir，返回本地图片路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    urls = post.image_urls[: config.INSTAGRAM_MAX_IMAGES_PER_POST]
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            out_path = out_dir / f"{post.post_id}_{idx:02d}.jpg"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                saved.append(out_path)
                logger.info("已下载图片 %s", out_path)
            except Exception:
                logger.exception("下载图片失败 post=%s url=%s", post.post_id, url)
    return saved


def run() -> None:
    _setup_logging()
    logger.info("开始 Instagram 监控，监控池：%s", config.INSTAGRAM_PROFILES)

    object_total = 0
    for profile in config.INSTAGRAM_PROFILES:
        try:
            posts = fetch_latest_posts(profile, config.INSTAGRAM_POSTS_PER_PROFILE)
        except Exception:
            logger.exception("抓取账号失败：%s", profile)
            continue

        safe_user = profile.strip().lstrip("@").replace("/", "_")
        for post in posts:
            post_dir = config.INSTAGRAM_OUTPUT_DIR / safe_user / post.post_id
            images = _download_images(post, post_dir)
            if not images:
                logger.warning("帖子无可用图片，跳过 post=%s", post.post_id)
                continue

            try:
                result = recognize_images(images, known_ip=post.username)
            except Exception:
                logger.exception("识图失败 post=%s", post.post_id)
                continue

            result["post_id"] = post.post_id
            result["username"] = post.username
            result["post_url"] = post.url
            result["timestamp"] = post.timestamp
            result["caption"] = post.caption
            out_path = post_dir / "recognition.json"
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            objects = result.get("objects", [])
            object_total += len(objects)
            logger.info(
                "识图完成 account=%s post=%s 物件数=%d → %s",
                post.username, post.post_id, len(objects), out_path,
            )

    logger.info("全部完成，累计识别物件 %d 个，输出目录 %s", object_total, config.INSTAGRAM_OUTPUT_DIR)


if __name__ == "__main__":
    run()

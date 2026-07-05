"""用 Apify 抓取 Instagram 名人账号的最新帖子（复用项目 .env 的 APIFY_API_KEY）。

仅取帖子里的图片 URL（含轮播多图、视频帖的封面图），供下游下载并识图。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from apify_client import ApifyClient

from app.core.apify_utils import apify_run_field

from . import config

logger = logging.getLogger(__name__)


@dataclass
class InstagramPost:
    post_id: str
    username: str
    caption: str
    url: str
    timestamp: str
    image_urls: list[str] = field(default_factory=list)


def _normalize_username(profile: str) -> str:
    return profile.strip().lstrip("@").rstrip("/").split("/")[-1]


def _collect_image_urls(item: dict) -> list[str]:
    """从单条帖子里收集图片 URL：主图 + 轮播子图 + 子帖封面。"""
    urls: list[str] = []
    display = item.get("displayUrl")
    if display:
        urls.append(display)
    images = item.get("images")
    if isinstance(images, list):
        urls.extend(u for u in images if isinstance(u, str))
    for child in item.get("childPosts") or []:
        if isinstance(child, dict) and child.get("displayUrl"):
            urls.append(child["displayUrl"])

    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _item_to_post(item: dict, username: str) -> InstagramPost | None:
    post_id = item.get("id") or item.get("shortCode") or item.get("shortcode")
    if not post_id:
        return None
    short_code = item.get("shortCode") or item.get("shortcode")
    url = item.get("url") or (f"https://www.instagram.com/p/{short_code}/" if short_code else "")
    image_urls = _collect_image_urls(item)
    if not image_urls:
        return None
    return InstagramPost(
        post_id=str(post_id),
        username=item.get("ownerUsername") or username,
        caption=item.get("caption") or "",
        url=url,
        timestamp=item.get("timestamp") or "",
        image_urls=image_urls,
    )


def fetch_latest_posts(profile: str, max_posts: int) -> list[InstagramPost]:
    """抓取某 Instagram 账号最新的 max_posts 条帖子。"""
    token = config.get_apify_api_key()
    if not token:
        raise RuntimeError("缺少 APIFY_API_KEY：请在项目根 .env 中配置。")

    username = _normalize_username(profile)
    actor_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "posts",
        "resultsLimit": max_posts,
        "addParentData": False,
    }

    client = ApifyClient(token)
    run = client.actor(config.APIFY_INSTAGRAM_ACTOR).call(run_input=actor_input)
    dataset_id = apify_run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise RuntimeError("Apify run 缺少 defaultDatasetId")
    raw = list(client.dataset(dataset_id).iterate_items())
    logger.info(
        "Instagram Apify 抓取完成 account=%s run_id=%s status=%s raw_count=%d",
        username,
        apify_run_field(run, "id"),
        apify_run_field(run, "status"),
        len(raw),
    )

    posts: list[InstagramPost] = []
    for item in raw[:max_posts]:
        if not isinstance(item, dict):
            continue
        post = _item_to_post(item, username)
        if post:
            posts.append(post)

    logger.info("账号 %s 解析出有效帖子 %d 条", username, len(posts))
    return posts

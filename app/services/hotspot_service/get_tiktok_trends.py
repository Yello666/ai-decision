from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from apify_client import ApifyClient
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.schemas.hotspot import CollectTrendObject, SentimentCN, TikTokHashtagTrendRequest
from app.services.hotspot_service.collect_hostspot import analyze_collect_trend_items_async
from app.services.hotspot_service.tiktok_hashtag_cache import get_tiktok_hashtag_analyzed_cached

logger = logging.getLogger(__name__)

_HASHTAG_RE = re.compile(r"#([\w\-\u4e00-\u9fff]+)")
_SUBTITLE_KEYS = {"subtitle", "subtitles", "caption", "captions", "transcript", "transcripts"}
_COMMENT_KEYS = {"comments", "commentlist", "topcomments"}


def _get_nested(item: dict[str, Any], path: str) -> Any:
    current: Any = item
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_value(item: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _get_nested(item, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float_metric(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return _parse_datetime(int(stripped))
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _stringify_text(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and not stripped.startswith(("http://", "https://")):
            return [stripped]
        return []
    if isinstance(value, dict):
        texts: list[str] = []
        for key in ("text", "content", "value", "line"):
            texts.extend(_stringify_text(value.get(key)))
        return texts
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(_stringify_text(item))
        return texts
    return []


def _collect_keyed_text(value: Any, target_keys: set[str]) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if lowered in target_keys:
                texts.extend(_stringify_text(child))
            texts.extend(_collect_keyed_text(child, target_keys))
    elif isinstance(value, list):
        for child in value:
            texts.extend(_collect_keyed_text(child, target_keys))
    return texts


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _extract_tags(item: dict[str, Any], fallback_text: str) -> list[str]:
    tags: list[str] = []
    for value in (
        _first_value(item, ("hashtags", "challenges", "textExtra")),
        _first_value(item, ("authorMeta.signature",)),
    ):
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    tags.append(entry)
                elif isinstance(entry, dict):
                    tag = _first_value(entry, ("name", "title", "hashtagName", "tagName"))
                    if tag:
                        tags.append(str(tag))
        elif isinstance(value, str):
            tags.extend(_HASHTAG_RE.findall(value))

    tags.extend(_HASHTAG_RE.findall(fallback_text or ""))
    normalized = [tag.strip().lstrip("#") for tag in tags if tag and tag.strip()]
    return _dedupe_keep_order(normalized)[:10]


def _extract_content_context(item: dict[str, Any]) -> tuple[str, list[str]]:
    description = str(
        _first_value(item, ("text", "description", "videoDescription", "title", "desc")) or ""
    ).strip()
    subtitles = _dedupe_keep_order(_collect_keyed_text(item, _SUBTITLE_KEYS))
    comments = _dedupe_keep_order(_collect_keyed_text(item, _COMMENT_KEYS))

    parts: list[str] = []
    if description:
        parts.append(f"视频描述：{description}")
    if subtitles:
        parts.append(f"字幕：{' '.join(subtitles[:20])}")
    if comments:
        parts.append(f"评论：{' '.join(comments[:20])}")

    context = "\n".join(parts).strip()
    return context or description or "TikTok 热点视频", subtitles


def _build_jump_url(item: dict[str, Any], video_id: str) -> str:
    value = _first_value(item, ("webVideoUrl", "url", "shareUrl", "videoUrl"))
    if value:
        return str(value)
    author = _first_value(item, ("authorMeta.name", "authorMeta.username", "author.uniqueId"))
    if author and video_id:
        return f"https://www.tiktok.com/@{str(author).lstrip('@')}/video/{video_id}"
    return "https://www.tiktok.com/"


def _item_to_collect_trend(item: dict[str, Any]) -> CollectTrendObject | None:
    raw_id = _first_value(item, ("id", "videoId", "awemeId", "itemId"))
    if raw_id is None:
        raw_id = _first_value(item, ("webVideoUrl", "url", "shareUrl"))
    if raw_id is None:
        logger.warning("TikTok item 缺少可用 id，已跳过")
        return None

    video_id = str(raw_id)
    context, subtitles = _extract_content_context(item)
    tags = _extract_tags(item, context)
    title_seed = str(_first_value(item, ("text", "description", "title", "desc")) or "").strip()
    if not title_seed and subtitles:
        title_seed = subtitles[0]
    if not title_seed and tags:
        title_seed = " ".join(f"#{tag}" for tag in tags[:3])
    title = title_seed[:80] if title_seed else "TikTok 热点视频"

    created_at = _parse_datetime(
        _first_value(item, ("createTimeISO", "createTime", "createdAt", "create_time"))
    )
    publish_time = (created_at or datetime.now(timezone.utc)).isoformat()

    return CollectTrendObject(
        id=video_id,
        title=title,
        summary=context,
        tags=tags,
        sentiment_label=SentimentCN.neutral,
        sentiment_score=0.0,
        audience=None,
        jump_url=_build_jump_url(item, video_id),
        view_count=_as_int(_first_value(item, ("playCount", "plays", "views", "stats.playCount"))),
        likes=_as_int(_first_value(item, ("diggCount", "diggs", "likes", "stats.diggCount"))),
        comment_count=_as_int(_first_value(item, ("commentCount", "comments", "stats.commentCount"))),
        share_count=_as_int(_first_value(item, ("shareCount", "shares", "stats.shareCount"))),
        collect_count=_as_int(_first_value(item, ("collectCount", "collects", "stats.collectCount"))),
        author_followers=_as_int(
            _first_value(item, ("authorMeta.fans", "authorStats.followerCount", "author.followers")),
        ),
        duration_seconds=_as_float_metric(
            _first_value(item, ("videoMeta.duration", "video.duration", "duration")),
        ),
        publish_time=publish_time,
        platform="TikTok",
    )


def _collect_trend_sort_key(item: CollectTrendObject, sort_by: str) -> float:
    """与 ``app/api/v1/tiktok.py`` 中 `_sort_value` 对齐（作用于 CollectTrendObject）。"""
    if sort_by == "diggs":
        value = float(item.likes)
    elif sort_by == "play_count":
        value = float(item.view_count)
    elif sort_by == "comments":
        value = float(item.comment_count)
    elif sort_by == "shares":
        value = float(item.share_count)
    elif sort_by == "collects":
        value = float(item.collect_count)
    elif sort_by == "followers":
        value = float(item.author_followers)
    elif sort_by == "duration":
        value = float(item.duration_seconds)
    elif sort_by == "engagement_rate":
        plays = item.view_count
        if not plays:
            return -1.0
        interactions = float(item.likes + item.comment_count + item.share_count)
        value = interactions / plays * 100
    else:
        created_at = _parse_datetime(item.publish_time)
        value = created_at.timestamp() if created_at else -1.0
    return value


def _sort_collect_trends(
    items: list[CollectTrendObject],
    *,
    sort_by: Literal[
        "diggs",
        "play_count",
        "comments",
        "shares",
        "collects",
        "followers",
        "duration",
        "engagement_rate",
        "create_time",
    ],
    sort_order: Literal["asc", "desc"],
    limit: int,
) -> list[CollectTrendObject]:
    ordered = sorted(
        items,
        key=lambda x: _collect_trend_sort_key(x, sort_by),
        reverse=sort_order == "desc",
    )
    return ordered[:limit]


def _build_hashtag_actor_input(request: TikTokHashtagTrendRequest) -> dict[str, Any]:
    return {
        "hashtags": request.hashtags,
        "resultsPerPage": request.max_results,
        "profiles": [],
        "searchQueries": [],
        "excludePinnedPosts": False,
        # Apify Actor 要求 postURLs 为数组；传 null 会校验失败。
        "postURLs": [],
        "scrapeRelatedVideos": False,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "downloadSubtitlesOptions": "DOWNLOAD_SUBTITLES",
        "commentsPerPost": request.comments_per_post,
        "topLevelCommentsPerPost": request.comments_per_post,
        "maxRepliesPerComment": request.max_replies_per_comment,
        "proxyCountryCode": "None",
    }


def _fetch_tiktok_items(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    token = settings.APIFY_API_KEY
    if not token:
        raise RuntimeError("缺少 APIFY_API_KEY 环境变量")

    actor_id = settings.APIFY_TIKTOK_ACTOR_ID
    if not actor_id:
        raise RuntimeError("缺少 APIFY_TIKTOK_ACTOR_ID 环境变量")

    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=actor_input)
    dataset_id = run.get("defaultDatasetId")
    raw = list(client.dataset(dataset_id).iterate_items())
    first_keys: list[str] | None = None
    if raw and isinstance(raw[0], dict):
        first_keys = sorted(raw[0].keys())[:40]
    logger.info(
        "TikTok Apify 抓取完成 actor_id=%s run_id=%s status=%s dataset_id=%s raw_count=%s first_item_keys=%s",
        actor_id,
        run.get("id"),
        run.get("status"),
        dataset_id,
        len(raw),
        first_keys,
    )
    return raw


async def _fetch_parse_analyze_tiktok_hashtags(
    request: TikTokHashtagTrendRequest,
) -> list[CollectTrendObject]:
    """抓取 TikTok hashtag 热点视频，并完成统一热点分析（缓存回源用）。"""
    actor_input = _build_hashtag_actor_input(request)
    logger.info(
        "TikTok hashtag 趋势 Actor 入参 hashtags=%s resultsPerPage=%s commentsPerPost=%s",
        actor_input.get("hashtags"),
        actor_input.get("resultsPerPage"),
        actor_input.get("commentsPerPost"),
    )
    raw_items = await run_in_threadpool(_fetch_tiktok_items, actor_input)
    slice_items = raw_items[: request.max_results]
    trends = [
        trend for item in slice_items if (trend := _item_to_collect_trend(item)) is not None
    ]
    parse_skipped = len(slice_items) - len(trends)
    logger.info(
        "TikTok hashtag 趋势 解析 raw_slice=%d 有效 CollectTrendObject=%d 解析跳过=%d",
        len(slice_items),
        len(trends),
        parse_skipped,
    )
    if not trends:
        logger.warning(
            "TikTok hashtag 趋势 无有效条目提前返回空列表（请对照上文 Apify raw_count 与解析跳过）"
        )
        return []
    analyzed = await analyze_collect_trend_items_async(trends)
    logger.info("TikTok hashtag 趋势 LLM/缓存分析后条数=%d", len(analyzed))
    return analyzed


async def collect_tiktok_hashtag_trends_async(
    request: TikTokHashtagTrendRequest,
) -> list[CollectTrendObject]:
    """抓取 TikTok hashtag 热点视频，分析后返回统一热点结构。"""

    analyzed = await get_tiktok_hashtag_analyzed_cached(
        request,
        loader=lambda: _fetch_parse_analyze_tiktok_hashtags(request),
    )
    out = _sort_collect_trends(
        analyzed,
        sort_by=request.sort.sort_by,
        sort_order=request.sort.sort_order,
        limit=request.sort.limit,
    )
    logger.info(
        "TikTok hashtag 趋势 排序后返回 sort_by=%s sort_order=%s limit=%s 实际返回=%d",
        request.sort.sort_by,
        request.sort.sort_order,
        request.sort.limit,
        len(out),
    )
    return out

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from apify_client import ApifyClient
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.responses import success
from app.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tiktok", tags=["tiktok"])

_ACTOR_INPUT_EXCLUDES = {
    "filters",
    "limit",
    "sort_by",
    "sort_order",
}

@router.post("/hashtag/hot-videos", summary="搜索 TikTok Hashtag 热点视频")
async def search_hashtag_hot_videos(payload: TiktokHashtagHotVideosRequest):
    """
    使用 Apify TikTok Scraper 搜索 hashtag 下的视频，并在服务端进行过滤、排序和截断。

    Apify Actor 参数直接放在请求体顶层，例如 `hashtags`、`resultsPerPage`、
    `videoSearchSorting`、`videoSearchDateFilter` 等；`filters` 用于本接口二次过滤。
    """
    actor_input = payload.to_actor_input()

    try:
        run, items = await run_in_threadpool(_fetch_tiktok_items, actor_input)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Apify TikTok Scraper 调用失败")
        raise HTTPException(status_code=502, detail=f"Apify TikTok Scraper 调用失败：{exc}")

    filtered_items = [item for item in items if _passes_filters(item, payload.filters)]
    filtered_items.sort(
        key=lambda item: _sort_value(item, payload.sort_by),
        reverse=payload.sort_order == "desc",
    )
    limited_items = filtered_items[: payload.limit]

    return success(
        data={
            "items": limited_items,
            "total_before_filter": len(items),
            "total_after_filter": len(filtered_items),
            "returned_count": len(limited_items),
            "actor_run": {
                "id": run.get("id"),
                "status": run.get("status"),
                "defaultDatasetId": run.get("defaultDatasetId"),
            },
            "actor_input": actor_input,
            "filters": payload.filters.model_dump(exclude_none=True),
            "sort_by": payload.sort_by,
            "sort_order": payload.sort_order,
        }
    )



class TiktokVideoFilters(BaseModel):
    """服务端二次过滤条件，字段缺省时不参与过滤。"""

    keyword: str | None = Field(default=None, description="视频文案关键词，大小写不敏感")
    author_username: str | None = Field(default=None, description="作者用户名，大小写不敏感")
    verified_only: bool | None = Field(default=None, description="仅返回认证作者视频")
    earliest_post_date: datetime | None = Field(default=None, description="最早发布时间")
    latest_post_date: datetime | None = Field(default=None, description="最晚发布时间")
    min_diggs: int | None = Field(default=None, ge=0, description="最低点赞数")
    max_diggs: int | None = Field(default=None, ge=0, description="最高点赞数")
    min_play_count: int | None = Field(default=None, ge=0, description="最低播放数")
    max_play_count: int | None = Field(default=None, ge=0, description="最高播放数")
    min_comments: int | None = Field(default=None, ge=0, description="最低评论数")
    min_shares: int | None = Field(default=None, ge=0, description="最低分享数")
    min_collects: int | None = Field(default=None, ge=0, description="最低收藏数")
    min_followers: int | None = Field(default=None, ge=0, description="作者最低粉丝数")
    min_duration_seconds: float | None = Field(default=None, ge=0, description="最短视频时长")
    max_duration_seconds: float | None = Field(default=None, ge=0, description="最长视频时长")
    min_engagement_rate: float | None = Field(
        default=None,
        ge=0,
        description="最低互动率百分比，计算方式：(赞+评+分享)/播放*100",
    )

    @field_validator("earliest_post_date", "latest_post_date")
    @classmethod
    def _ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


class TiktokHashtagHotVideosRequest(BaseModel):
    """
    TikTok Scraper Actor 输入参数 + 本接口过滤参数。

    未显式声明的新 Actor 参数也可以放在请求体顶层，会原样透传给 Apify。
    """

    model_config = ConfigDict(extra="allow")

    hashtags: list[str] = Field(..., min_length=1, description="要搜索的 hashtag，例如 ['fyp']")
    resultsPerPage: int = Field(default=100, ge=1, le=1000, description="Actor 每页抓取数量")
    profiles: list[str] | None = Field(default=None, description="指定 TikTok profile")
    profileScrapeSections: list[str] | None = Field(
        default_factory=lambda: ["videos"],
        description="profile 抓取区域",
    )
    profileSorting: str = Field(default="latest", description="profile 视频排序")
    excludePinnedPosts: bool = Field(default=False, description="是否排除置顶视频")
    oldestPostDateUnified: str | None = Field(default=None, description="最早发布日期")
    newestPostDate: str | None = Field(default=None, description="最新发布日期")
    mostDiggs: int | None = Field(default=None, ge=0, description="Actor 点赞上限")
    leastDiggs: int | None = Field(default=None, ge=0, description="Actor 点赞下限")
    maxFollowersPerProfile: int = Field(default=0, ge=0, description="每个 profile 最大粉丝数")
    maxFollowingPerProfile: int = Field(default=0, ge=0, description="每个 profile 最大关注数")
    searchQueries: list[str] | None = Field(default=None, description="视频/用户搜索词")
    searchSection: str = Field(default="", description="搜索区域")
    maxProfilesPerQuery: int = Field(default=10, ge=0, description="每个搜索词最大 profile 数")
    videoSearchSorting: str = Field(default="MOST_RELEVANT", description="视频搜索排序")
    videoSearchDateFilter: str = Field(default="ALL_TIME", description="视频搜索时间范围")
    postURLs: list[str] | None = Field(default=None, description="指定视频 URL")
    scrapeRelatedVideos: bool = Field(default=False, description="是否抓取相关视频")
    shouldDownloadVideos: bool = Field(default=False, description="是否下载视频")
    shouldDownloadCovers: bool = Field(default=False, description="是否下载封面")
    shouldDownloadSlideshowImages: bool = Field(default=False, description="是否下载图集图片")
    shouldDownloadAvatars: bool = Field(default=False, description="是否下载头像")
    shouldDownloadMusicCovers: bool = Field(default=False, description="是否下载音乐封面")
    videoKvStoreIdOrName: str | None = Field(default=None, description="视频下载 KV store")
    downloadSubtitlesOptions: str = Field(
        default="NEVER_DOWNLOAD_SUBTITLES",
        description="字幕下载策略",
    )
    commentsPerPost: int = Field(default=0, ge=0, description="每条视频抓取评论数")
    topLevelCommentsPerPost: int = Field(default=0, ge=0, description="每条视频抓取顶层评论数")
    maxRepliesPerComment: int = Field(default=0, ge=0, description="每条评论最大回复数")
    proxyCountryCode: str = Field(default="None", description="代理国家代码")

    filters: TiktokVideoFilters = Field(default_factory=TiktokVideoFilters, description="服务端过滤条件")
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
    ] = Field(default="diggs", description="返回结果排序字段")
    sort_order: Literal["asc", "desc"] = Field(default="desc", description="排序方向")
    limit: int = Field(default=50, ge=1, le=500, description="过滤排序后最多返回条数")

    @field_validator("hashtags")
    @classmethod
    def _normalize_hashtags(cls, value: list[str]) -> list[str]:
        normalized = [tag.strip().lstrip("#") for tag in value if tag and tag.strip()]
        if not normalized:
            raise ValueError("hashtags 不能为空")
        return normalized

    def to_actor_input(self) -> dict[str, Any]:
        return self.model_dump(exclude=_ACTOR_INPUT_EXCLUDES)


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
        if value not in (None, ""):
            return value
    return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


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


def _diggs(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("diggCount", "diggs", "likes", "stats.diggCount")))


def _play_count(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("playCount", "plays", "views", "stats.playCount")))


def _comments(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("commentCount", "comments", "stats.commentCount")))


def _shares(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("shareCount", "shares", "stats.shareCount")))


def _collects(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("collectCount", "collects", "stats.collectCount")))


def _followers(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("authorMeta.fans", "authorStats.followerCount", "author.followers")))


def _duration(item: dict[str, Any]) -> float | None:
    return _as_float(_first_value(item, ("videoMeta.duration", "video.duration", "duration")))


def _create_time(item: dict[str, Any]) -> datetime | None:
    return _parse_datetime(_first_value(item, ("createTimeISO", "createTime", "createdAt", "create_time")))


def _engagement_rate(item: dict[str, Any]) -> float | None:
    plays = _play_count(item)
    if not plays:
        return None
    interactions = (_diggs(item) or 0) + (_comments(item) or 0) + (_shares(item) or 0)
    return interactions / plays * 100


def _text_blob(item: dict[str, Any]) -> str:
    parts = [
        _first_value(item, ("text", "description", "videoDescription", "title")),
        _first_value(item, ("authorMeta.name", "authorMeta.nickName", "author.nickname")),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _author_username(item: dict[str, Any]) -> str | None:
    value = _first_value(item, ("authorMeta.name", "authorMeta.username", "author.uniqueId", "author"))
    return str(value).lower() if value else None


def _passes_range(value: float | None, minimum: float | None, maximum: float | None) -> bool:
    if minimum is not None and (value is None or value < minimum):
        return False
    if maximum is not None and (value is None or value > maximum):
        return False
    return True


def _passes_filters(item: dict[str, Any], filters: TiktokVideoFilters) -> bool:
    if filters.keyword and filters.keyword.lower() not in _text_blob(item):
        return False

    if filters.author_username:
        username = _author_username(item)
        if username is None or filters.author_username.lower().lstrip("@") not in username:
            return False

    if filters.verified_only is True:
        verified = _as_bool(_first_value(item, ("authorMeta.verified", "author.verified", "verified")))
        if verified is not True:
            return False

    created_at = _create_time(item)
    if filters.earliest_post_date is not None and (created_at is None or created_at < filters.earliest_post_date):
        return False
    if filters.latest_post_date is not None and (created_at is None or created_at > filters.latest_post_date):
        return False

    checks = (
        (_diggs(item), filters.min_diggs, filters.max_diggs),
        (_play_count(item), filters.min_play_count, filters.max_play_count),
        (_comments(item), filters.min_comments, None),
        (_shares(item), filters.min_shares, None),
        (_collects(item), filters.min_collects, None),
        (_followers(item), filters.min_followers, None),
        (_duration(item), filters.min_duration_seconds, filters.max_duration_seconds),
        (_engagement_rate(item), filters.min_engagement_rate, None),
    )
    return all(_passes_range(value, minimum, maximum) for value, minimum, maximum in checks)


def _sort_value(item: dict[str, Any], sort_by: str) -> float:
    if sort_by == "diggs":
        value = _diggs(item)
    elif sort_by == "play_count":
        value = _play_count(item)
    elif sort_by == "comments":
        value = _comments(item)
    elif sort_by == "shares":
        value = _shares(item)
    elif sort_by == "collects":
        value = _collects(item)
    elif sort_by == "followers":
        value = _followers(item)
    elif sort_by == "duration":
        value = _duration(item)
    elif sort_by == "engagement_rate":
        value = _engagement_rate(item)
    else:
        created_at = _create_time(item)
        value = created_at.timestamp() if created_at else None
    return value if value is not None else -1


def _fetch_tiktok_items(actor_input: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settings=get_settings()
    token = settings.APIFY_API_KEY
    if not token:
        raise RuntimeError("缺少 APIFY_API_KEY 环境变量")

    actor_id = settings.APIFY_TIKTOK_ACTOR_ID
    if not actor_id:
        raise RuntimeError("缺少 APIFY_TIKTOK_ACTOR_ID 环境变量")

    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=actor_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return run, items



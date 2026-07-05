from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from apify_client import ApifyClient
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.apify_utils import apify_run_field
from app.core.config import get_settings
from app.core.responses import success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tiktok", tags=["tiktok"])

_SERVER_SIDE_FIELDS = frozenset({"filters", "sort_by", "sort_order", "limit"})


class TiktokVideoFilters(BaseModel):
    """
    服务端二次过滤：全部字段可选；不传表示不参与该维度过滤。

    关键词、作者、认证、发布时间、点赞上下限等由 Apify Actor 输入参数控制，此处不再重复暴露。

    三个接口 ``/tiktok/hashtag/hot-videos``、``/tiktok/profile/videos``、``/tiktok/search/videos`` 共用。
    """
    min_play_count: int | None = Field(default=None, ge=0, description="播放次数下限；示例：100000")
    max_play_count: int | None = Field(default=None, ge=0, description="播放次数上限")
    min_comments: int | None = Field(default=None, ge=0, description="评论数下限")
    min_shares: int | None = Field(default=None, ge=0, description="分享数下限")
    min_collects: int | None = Field(default=None, ge=0, description="收藏数下限")
    min_followers: int | None = Field(default=None, ge=0, description="作者粉丝数下限")
    min_duration_seconds: float | None = Field(default=None, ge=0, description="视频时长下限（秒）")
    max_duration_seconds: float | None = Field(default=None, ge=0, description="视频时长上限（秒）")
    min_engagement_rate: float | None = Field(
        default=None,
        ge=0,
        description="互动率下限（百分比）：(赞+评+分享)/播放×100；无播放量时无法满足此项",
    )


class TiktokActorUniversalOptions(BaseModel):
    """
    三类抓取接口共用的 Apify Actor 可选参数（与 Console Input 字段同名）。

    支持任意额外字段透传 Actor（``extra="allow"``）。
    """

    model_config = ConfigDict(extra="allow")

    resultsPerPage: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="Actor 单次抓取结果规模；范围 1～1000，默认 50",
    )
    excludePinnedPosts: bool = Field(
        default=False,
        description="true：排除置顶帖；false：不排除",
    )
    oldestPostDateUnified: str | None = Field(
        default=None,
        description="Actor 最早发帖日期（格式以 Apify Input 为准）；未设置时不写入 Actor 输入（勿传 null）",
    )
    newestPostDate: str | None = Field(
        default=None,
        description="Actor 最新发帖日期；未设置时不写入 Actor 输入（勿传 null）",
    )
    mostDiggs: int | None = Field(
        default=None,
        ge=0,
        description="Actor 点赞上限；null 表示不限制",
    )
    leastDiggs: int | None = Field(
        default=None,
        ge=0,
        description="Actor 点赞下限；null 表示不限制",
    )
    maxFollowersPerProfile: int = Field(
        default=0,
        ge=0,
        description="单 profile 粉丝上限；0 含义以 Actor 为准",
    )
    maxFollowingPerProfile: int = Field(
        default=0,
        ge=0,
        description="单 profile 关注上限；0 含义以 Actor 为准",
    )
    postURLs: list[str] | None = Field(
        default=None,
        description="直接指定 TikTok 视频 URL 列表；不使用则 null",
    )
    scrapeRelatedVideos: bool = Field(default=False, description="是否额外抓取相关视频")
    shouldDownloadVideos: bool = Field(default=False, description="是否下载视频文件")
    shouldDownloadCovers: bool = Field(default=False, description="是否下载封面图")
    shouldDownloadSlideshowImages: bool = Field(default=False, description="是否下载图集图片")
    shouldDownloadAvatars: bool = Field(default=False, description="是否下载头像")
    shouldDownloadMusicCovers: bool = Field(default=False, description="是否下载音乐封面")
    videoKvStoreIdOrName: str | None = Field(
        default=None,
        description="下载产物使用的 KV Store ID 或名称",
    )
    downloadSubtitlesOptions: str = Field(
        default="NEVER_DOWNLOAD_SUBTITLES",
        description='字幕策略；默认 NEVER_DOWNLOAD_SUBTITLES；其它枚举见 Actor Input',
    )
    commentsPerPost: int = Field(default=0, ge=0, description="每条帖子抓取评论条数；0 表示不抓")
    topLevelCommentsPerPost: int = Field(default=0, ge=0, description="每条帖子顶层评论条数")
    maxRepliesPerComment: int = Field(default=0, ge=0, description="每条评论最多抓取回复数")
    proxyCountryCode: str = Field(
        default="None",
        description='代理国家代码；默认字符串 "None" 表示不按国家筛选（与 Actor 示例一致）',
    )


class TiktokListQueryParams(BaseModel):
    """服务端：过滤、排序、返回条数（不传给 Apify）。"""

    filters: TiktokVideoFilters = Field(
        default_factory=TiktokVideoFilters,
        description="服务端过滤；默认空对象表示不过滤",
    )
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
    ] = Field(default="diggs", description="排序字段")
    sort_order: Literal["asc", "desc"] = Field(default="desc", description="asc 升序 / desc 降序")
    limit: int = Field(default=50, ge=1, le=500, description="过滤排序后最多返回条数")


class TiktokHashtagVideosRequest(TiktokActorUniversalOptions, TiktokListQueryParams):
    """仅 Hashtag 场景：只需传 ``hashtags``；不必传 ``profiles`` / ``searchQueries``。"""

    hashtags: list[str] = Field(
        ...,
        min_length=1,
        description='标签列表，可有或无 #。示例：["fyp"]',
    )
    model_config = ConfigDict(extra="allow")

    videoSearchSorting: str = Field(
        default="MOST_RELEVANT",
        description='Hashtag/搜索排序；示例 MOST_RELEVANT；完整枚举以 Actor Input 为准',
    )
    videoSearchDateFilter: str = Field(
        default="ALL_TIME",
        description='时间范围；示例 ALL_TIME；其它如 LAST_7_DAYS 以 Actor 为准',
    )

    @field_validator("hashtags")
    @classmethod
    def _normalize_hashtags(cls, value: list[str]) -> list[str]:
        normalized = [tag.strip().lstrip("#") for tag in value if tag and tag.strip()]
        if not normalized:
            raise ValueError("hashtags 不能为空")
        return normalized

    def to_actor_input(self) -> dict[str, Any]:
        data = self.model_dump(exclude=_SERVER_SIDE_FIELDS, exclude_none=True)
        # Actor Input 要求 profiles / searchQueries 为 array，不能为 null
        data["profiles"] = []
        data["searchQueries"] = []
        return data


class TiktokProfileVideosRequest(TiktokActorUniversalOptions, TiktokListQueryParams):
    """仅 Profile 主页场景：只需传 ``profiles``；不必传 ``hashtags`` / ``searchQueries``。"""

    model_config = ConfigDict(extra="allow")

    profiles: list[str] = Field(
        ...,
        min_length=1,
        description='TikTok 用户名列表（含或不含 @ 均可）。示例：["tiktok"]',
    )
    profileScrapeSections: list[str] = Field(
        default_factory=lambda: ["videos"],
        description='主页抓取区块；默认 ["videos"]；枚举以 Actor Input 为准',
    )
    profileSorting: str = Field(
        default="latest",
        description='主页视频排序；默认 "latest"；可选值以 Actor 为准',
    )

    @field_validator("profiles")
    @classmethod
    def _normalize_profiles(cls, value: list[str]) -> list[str]:
        normalized = [p.strip().lstrip("@") for p in value if p and p.strip()]
        if not normalized:
            raise ValueError("profiles 不能为空")
        return normalized

    def to_actor_input(self) -> dict[str, Any]:
        data = self.model_dump(exclude=_SERVER_SIDE_FIELDS, exclude_none=True)
        data["hashtags"] = []
        data["searchQueries"] = []
        return data


class TiktokKeywordSearchVideosRequest(TiktokActorUniversalOptions, TiktokListQueryParams):
    """仅关键词搜索场景：只需传 ``searchQueries``；不必传 ``hashtags`` / ``profiles``。"""

    model_config = ConfigDict(extra="allow")

    searchQueries: list[str] = Field(
        ...,
        min_length=1,
        description='搜索词列表。示例：["asmr sleep"]',
    )
    searchSection: str = Field(
        default="",
        description='搜索分区；默认 ""；可选值见 Actor Console Input',
    )
    maxProfilesPerQuery: int = Field(
        default=10,
        ge=0,
        description="每条搜索最多展开的 profile 数量",
    )
    videoSearchSorting: str = Field(
        default="MOST_RELEVANT",
        description='搜索结果视频排序；枚举以 Actor Input 为准',
    )
    videoSearchDateFilter: str = Field(
        default="ALL_TIME",
        description='发布时间过滤；枚举以 Actor Input 为准',
    )

    @field_validator("searchQueries")
    @classmethod
    def _normalize_queries(cls, value: list[str]) -> list[str]:
        normalized = [q.strip() for q in value if q and q.strip()]
        if not normalized:
            raise ValueError("searchQueries 不能为空")
        return normalized

    def to_actor_input(self) -> dict[str, Any]:
        data = self.model_dump(exclude=_SERVER_SIDE_FIELDS, exclude_none=True)
        data["hashtags"] = []
        data["profiles"] = []
        return data


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


def _passes_range(value: float | None, minimum: float | None, maximum: float | None) -> bool:
    if minimum is not None and (value is None or value < minimum):
        return False
    if maximum is not None and (value is None or value > maximum):
        return False
    return True


def _passes_filters(item: dict[str, Any], filters: TiktokVideoFilters) -> bool:
    checks = (
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
    settings = get_settings()
    token = settings.APIFY_API_KEY
    if not token:
        raise RuntimeError("缺少 APIFY_API_KEY 环境变量")

    actor_id = settings.APIFY_TIKTOK_ACTOR_ID
    if not actor_id:
        raise RuntimeError("缺少 APIFY_TIKTOK_ACTOR_ID 环境变量")

    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=actor_input)
    dataset_id = apify_run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise RuntimeError("Apify run 缺少 defaultDatasetId")
    items = list(client.dataset(dataset_id).iterate_items())
    return run, items


async def _run_search_and_respond(
    *,
    actor_input: dict[str, Any],
    filters: TiktokVideoFilters,
    sort_by: str,
    sort_order: Literal["asc", "desc"],
    limit: int,
) -> dict[str, Any]:
    try:
        run, items = await run_in_threadpool(_fetch_tiktok_items, actor_input)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Apify TikTok Scraper 调用失败")
        raise HTTPException(status_code=502, detail=f"Apify TikTok Scraper 调用失败：{exc}")

    filtered_items = [item for item in items if _passes_filters(item, filters)]
    filtered_items.sort(
        key=lambda item: _sort_value(item, sort_by),
        reverse=sort_order == "desc",
    )
    limited_items = filtered_items[:limit]

    return success(
        data={
            "items": limited_items,
            "total_before_filter": len(items),
            "total_after_filter": len(filtered_items),
            "returned_count": len(limited_items),
            "actor_run": {
                "id": apify_run_field(run, "id"),
                "status": apify_run_field(run, "status"),
                "defaultDatasetId": apify_run_field(run, "defaultDatasetId", "default_dataset_id"),
            },
            "actor_input": actor_input,
            "filters": filters.model_dump(exclude_none=True),
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
    )


_SHARED_DOC_TAIL = """
**配置（服务端）**

- ``APIFY_API_KEY``：Apify API Token。
- ``APIFY_TIKTOK_ACTOR_ID``：Actor ID。

**共用可选参数**

- ``TiktokActorUniversalOptions``：下载、评论深度、日期与点赞上下限、``resultsPerPage`` 等；三类接口均可追加，
  亦可通过 ``extra`` 透传 Actor 其它字段。

**服务端字段（不传给 Apify）**

- ``filters``：二次过滤，字段见 Schema。
- ``sort_by``：``diggs`` | ``play_count`` | ``comments`` | ``shares`` | ``collects`` | ``followers`` |
  ``duration`` | ``engagement_rate`` | ``create_time``。
- ``sort_order``：``asc`` | ``desc``。
- ``limit``：1～500。

**响应 ``data``**

- ``items``、``total_*``、``returned_count``、``actor_run``、``actor_input``、回显 ``filters`` / 排序参数。
"""


@router.post("/hashtag/hot-videos", summary="按 Hashtag 搜索热点视频")
async def search_hashtag_hot_videos(payload: TiktokHashtagVideosRequest):
    f"""
    **仅 Hashtag**：必填只有 ``hashtags``；不要传业务用的 ``profiles`` / ``searchQueries``（服务端会向 Actor 传空数组）。

    常用可选：``videoSearchSorting``、``videoSearchDateFilter``、``resultsPerPage``。

    **最小示例**

    ```json
    {{"hashtags": ["fyp"], "resultsPerPage": 50}}
    ```

    {_SHARED_DOC_TAIL}
    """
    actor_input = payload.to_actor_input()
    return await _run_search_and_respond(
        actor_input=actor_input,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_order=payload.sort_order,
        limit=payload.limit,
    )


@router.post("/profile/videos", summary="按 Profile 抓取主页视频")
async def search_profile_videos(payload: TiktokProfileVideosRequest):
    f"""
    **仅主页**：必填只有 ``profiles``；不要传业务用的 ``hashtags`` / ``searchQueries``（服务端会向 Actor 传空数组）。

    常用可选：``profileScrapeSections``（默认 ``["videos"]``）、``profileSorting``、``resultsPerPage``。

    **最小示例**

    ```json
    {{"profiles": ["tiktok"]}}
    ```

    {_SHARED_DOC_TAIL}
    """
    actor_input = payload.to_actor_input()
    return await _run_search_and_respond(
        actor_input=actor_input,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_order=payload.sort_order,
        limit=payload.limit,
    )


@router.post("/search/videos", summary="按关键词搜索视频")
async def search_keyword_videos(payload: TiktokKeywordSearchVideosRequest):
    f"""
    **仅关键词**：必填只有 ``searchQueries``；不要传业务用的 ``hashtags`` / ``profiles``（服务端会向 Actor 传空数组）。

    常用可选：``searchSection``、``maxProfilesPerQuery``、``videoSearchSorting``、``videoSearchDateFilter``。

    **最小示例**

    ```json
    {{"searchQueries": ["coffee recipe"]}}
    ```

    {_SHARED_DOC_TAIL}
    """
    actor_input = payload.to_actor_input()
    return await _run_search_and_respond(
        actor_input=actor_input,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_order=payload.sort_order,
        limit=payload.limit,
    )

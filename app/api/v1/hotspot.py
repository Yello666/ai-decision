import math
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.api.deps import get_current_merchant
from app.core.hot_trends_cache import get_hot_trends_cached
from app.db.mysql import get_db
from app.models import Merchant, MerchantHotspotRecommendEmailSchedule
from app.schemas.hotspot import (
    BrandObject,
    HotspotBatchMatchRequest,
    HotspotMatchResponse,
    HotspotRecommendEmailScheduleResponse,
    HotspotRecommendEmailScheduleStateResponse,
    HotspotRecommendEmailScheduleUpsertRequest,
    HotspotRecommendRequest,
    HotspotRecommendResponse,
    HotspotTrendRequest,
    PaginatedTrendResponse,
    RecommendEmailScheduleMode,
)
from app.services.hotspot_service.analyse_matching_degree import (
    batch_match_hotspot_for_brand_async,
)
from app.services.hotspot_service.collect_hostspot import collect_and_format_hot_data_async
from app.services.hotspot_service.recommend_prefs import get_recommend_prefs, sync_recommend_prefs
from app.services.hotspot_service.recommended_hotspots import build_recommended_hotspots
from app.services.merchant_service import get_brand_by_merchant_id

router = APIRouter(prefix="/hotspot", tags=["hotspot"])

# /recommend 固定与全量热点缓存同源：当前仅 YouTube；匹配模型与配置 LLM_MODEL_36_PLUS（/match 默认）一致。
_RECOMMEND_PLATFORMS: list[str] = ["youtube"]


@router.post("/hot-trends", response_model=PaginatedTrendResponse, summary="获取热点数据（分页）")
async def get_hot_trends(request: HotspotTrendRequest):
    """
    获取热点趋势数据（分页）。
    后端固定缓存 50 条全量数据，前端按 page / page_size 翻页，每页最多 10 条，最多 5 页。
    """
    try:
        all_trends = await get_hot_trends_cached(
            request.platforms,
            loader=collect_and_format_hot_data_async,
        )

        total = len(all_trends)
        total_pages = math.ceil(total / request.page_size) if total else 0
        start = (request.page - 1) * request.page_size
        end = start + request.page_size
        items = all_trends[start:end]

        return PaginatedTrendResponse(
            items=items,
            total=total,
            page=request.page,
            page_size=request.page_size,
            total_pages=total_pages,
        )
    except Exception as e:
        msg = str(e).replace("\n", " ").replace("\\n", " ").strip()
        raise HTTPException(status_code=500, detail=f"获取热点数据失败：{msg}")



# 2.热点批量匹配（需登录；品牌信息由服务端从当前商户读取）
@router.post("/match", response_model=List[HotspotMatchResponse])
async def match_hotspot(
    request: HotspotBatchMatchRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    批量热点匹配：
    - 需登录；品牌信息根据当前登录商户自动从数据库读取
    - 每个 (品牌, 热点) 组合只分析一次，命中缓存则复用
    - 未命中的热点并行批量调用 LLM（模型固定为配置项 LLM_MODEL_36_PLUS），Prompt 中品牌信息只写一次
    """
    brand_model = get_brand_by_merchant_id(db, current_merchant.id)
    if not brand_model:
        raise HTTPException(status_code=400, detail="brand_not_set")

    brand = BrandObject(
        name=brand_model.name,
        core_value=brand_model.core_value,
        mainly_sold_products=brand_model.mainly_sold_products,
        tone=brand_model.tone,
        audience=brand_model.audience.split(",") if brand_model.audience else [],
    )

    try:
        return await batch_match_hotspot_for_brand_async(
            trends=request.trends,
            brand=brand,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量匹配失败：{str(e)}")


@router.post(
    "/recommend",
    response_model=HotspotRecommendResponse,
    summary="推荐热点（全量匹配并按契合度过滤）",
)
async def recommend_hotspots(
    request: HotspotRecommendRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    对热点全量缓存列表中全部热点做品牌匹配，剔除契合度低于 min_compatibility_score 的条目后返回。
    请求中的 min_compatibility_score 会与 Redis 中该商户已存值比较，不一致则写回 Redis。
    平台固定为 YouTube；匹配分析固定使用配置项 LLM_MODEL_36_PLUS。
    """
    brand_model = get_brand_by_merchant_id(db, current_merchant.id)
    if not brand_model:
        raise HTTPException(status_code=400, detail="brand_not_set")

    await sync_recommend_prefs(
        current_merchant.id,
        request.min_compatibility_score,
    )

    brand = BrandObject(
        name=brand_model.name,
        core_value=brand_model.core_value,
        mainly_sold_products=brand_model.mainly_sold_products,
        tone=brand_model.tone,
        audience=brand_model.audience.split(",") if brand_model.audience else [],
    )

    try:
        items, analyzed_count = await build_recommended_hotspots(
            platforms=_RECOMMEND_PLATFORMS,
            min_compatibility_score=request.min_compatibility_score,
            brand=brand,
        )
    except Exception as e:
        msg = str(e).replace("\n", " ").replace("\\n", " ").strip()
        raise HTTPException(status_code=500, detail=f"推荐热点失败：{msg}")

    return HotspotRecommendResponse(
        items=items,
        min_compatibility_score=request.min_compatibility_score,
        analyzed_count=analyzed_count,
    )


def _schedule_row_to_state_response(
    row: MerchantHotspotRecommendEmailSchedule,
    *,
    configured: bool,
) -> HotspotRecommendEmailScheduleStateResponse:
    last = row.last_sent_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    triggered = row.last_triggered_at
    if triggered is not None and triggered.tzinfo is None:
        triggered = triggered.replace(tzinfo=timezone.utc)
    mode_value = row.mode or RecommendEmailScheduleMode.interval_from_now.value
    return HotspotRecommendEmailScheduleStateResponse(
        configured=configured,
        id=row.id if configured else None,
        merchant_id=row.merchant_id,
        is_enabled=bool(row.is_enabled),
        mode=RecommendEmailScheduleMode(mode_value),
        min_compatibility_score=float(row.min_compatibility_score),
        send_hour=int(row.send_hour),
        send_minute=int(row.send_minute),
        timezone=row.timezone,
        interval_hours=int(row.interval_hours),
        last_sent_at=last,
        last_triggered_at=triggered,
    )


def _schedule_row_to_response(row: MerchantHotspotRecommendEmailSchedule) -> HotspotRecommendEmailScheduleResponse:
    last = row.last_sent_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    triggered = row.last_triggered_at
    if triggered is not None and triggered.tzinfo is None:
        triggered = triggered.replace(tzinfo=timezone.utc)
    mode_value = row.mode or RecommendEmailScheduleMode.interval_from_now.value
    return HotspotRecommendEmailScheduleResponse(
        id=row.id,
        merchant_id=row.merchant_id,
        is_enabled=bool(row.is_enabled),
        mode=RecommendEmailScheduleMode(mode_value),
        min_compatibility_score=float(row.min_compatibility_score),
        send_hour=int(row.send_hour),
        send_minute=int(row.send_minute),
        timezone=row.timezone,
        interval_hours=int(row.interval_hours),
        last_sent_at=last,
        last_triggered_at=triggered,
    )


@router.put(
    "/recommend-email/schedule",
    response_model=HotspotRecommendEmailScheduleResponse,
    summary="开启或更新定时热点推荐邮件",
)
async def upsert_recommend_email_schedule(
    request: HotspotRecommendEmailScheduleUpsertRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    为当前登录商户创建或更新定时推荐邮件配置。
    支持三种 mode：
    - interval_from_now：从当前保存时刻起，每隔 interval_hours 触发
    - daily_fixed：按 timezone 下 send_hour:send_minute 每日触发一次
    - interval_from_fixed：按固定时刻触发窗口，且两次成功发送至少间隔 interval_hours
    关闭时：清空 `last_sent_at` 与 `last_triggered_at`。
    同时将 `min_compatibility_score` 写入 Redis，与 `/hotspot/recommend` 偏好一致。
    """
    brand_model = get_brand_by_merchant_id(db, current_merchant.id)
    if not brand_model:
        raise HTTPException(status_code=400, detail="brand_not_set")

    row = (
        db.query(MerchantHotspotRecommendEmailSchedule)
        .filter(MerchantHotspotRecommendEmailSchedule.merchant_id == current_merchant.id)
        .first()
    )
    row_is_new = row is None
    if row is None:
        row = MerchantHotspotRecommendEmailSchedule(merchant_id=current_merchant.id)
        db.add(row)

    old_enabled = bool(row.is_enabled)
    old_timing = None
    if not row_is_new:
        old_timing = (
            row.mode,
            int(row.send_hour),
            int(row.send_minute),
            row.timezone,
            int(row.interval_hours),
        )
    new_timing = (
        request.mode.value,
        request.send_hour,
        request.send_minute,
        request.timezone,
        request.interval_hours,
    )
    timing_changed = old_enabled and request.is_enabled and old_timing != new_timing

    row.is_enabled = request.is_enabled
    row.mode = request.mode.value
    row.min_compatibility_score = Decimal(str(round(request.min_compatibility_score, 2)))
    row.send_hour = request.send_hour
    row.send_minute = request.send_minute
    row.timezone = request.timezone
    row.interval_hours = request.interval_hours

    if request.is_enabled:
        if (not old_enabled) or (row.last_sent_at is None) or timing_changed:
            now_utc = datetime.now(timezone.utc)
            if request.mode == RecommendEmailScheduleMode.interval_from_now:
                row.last_sent_at = now_utc
            else:
                row.last_sent_at = None
            row.last_triggered_at = None
    else:
        row.last_sent_at = None
        row.last_triggered_at = None

    db.commit()
    db.refresh(row)

    await sync_recommend_prefs(current_merchant.id, request.min_compatibility_score)

    return _schedule_row_to_response(row)


@router.get(
    "/recommend-email/schedule",
    response_model=HotspotRecommendEmailScheduleStateResponse,
    summary="读取定时热点推荐邮件配置",
)
async def get_recommend_email_schedule(
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    进入页面时调用：若库中已有配置则 `configured=true` 并返回完整信息；
    否则 `configured=false`，`send_*` / `timezone` / `interval_hours` 为表单占位默认值，
    契合度优先取 Redis 中推荐偏好（无则 40）。
    """
    row = (
        db.query(MerchantHotspotRecommendEmailSchedule)
        .filter(MerchantHotspotRecommendEmailSchedule.merchant_id == current_merchant.id)
        .first()
    )
    if row is not None:
        return _schedule_row_to_state_response(row, configured=True)

    stored_min = await get_recommend_prefs(current_merchant.id)
    min_score = float(stored_min) if stored_min is not None else 40.0

    return HotspotRecommendEmailScheduleStateResponse(
        configured=False,
        id=None,
        merchant_id=current_merchant.id,
        is_enabled=False,
        mode=RecommendEmailScheduleMode.interval_from_now,
        min_compatibility_score=round(min_score, 2),
        send_hour=9,
        send_minute=0,
        timezone="Asia/Shanghai",
        interval_hours=24,
        last_sent_at=None,
        last_triggered_at=None,
    )


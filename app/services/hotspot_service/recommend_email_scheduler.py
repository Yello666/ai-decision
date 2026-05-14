from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.mysql import SessionLocal
from app.models import (
    Brand,
    Merchant,
    MerchantHotspotRecommendEmailDelivery,
    MerchantHotspotRecommendEmailSchedule,
)
from app.schemas.hotspot import BrandObject, RecommendEmailScheduleMode, TrendObject
from app.services.hotspot_service.match_cache import brand_fingerprint, trend_fingerprint
from app.services.hotspot_service.recommend_email import send_recommendation_email
from app.services.hotspot_service.recommended_hotspots import build_recommended_hotspots

logger = logging.getLogger(__name__)

_RECOMMEND_PLATFORMS: list[str] = ["youtube"]
_JOB_ID = "scheduled_hotspot_recommend_email"
"""
定时推荐邮件流程（简述）：
1) APScheduler 每分钟触发一次 run_recommend_email_scheduler_once；
2) 从 merchant_hotspot_recommend_email_schedule 查询 is_enabled=true 的配置；
3) 按 mode 判断是否到达发送窗口（interval_from_now / daily_fixed / interval_from_fixed）；
4) 到达窗口后执行热点推荐分析并发送邮件；
5) 发件前按 merchant + brand_fp + trend_fp 去重，只发送未发过热点。

涉及数据库字段：
- 读取：is_enabled、mode、interval_hours、send_hour/send_minute/timezone、last_sent_at、last_triggered_at
- 写入：last_sent_at、last_triggered_at，以及 merchant_hotspot_recommend_email_delivery
"""

def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_brand_object(brand: Brand) -> BrandObject:
    return BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        mainly_sold_products=brand.mainly_sold_products,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else [],
    )


def _should_send_now(schedule: MerchantHotspotRecommendEmailSchedule, now_utc: datetime) -> bool:
    if not schedule.is_enabled:
        return False

    mode_raw = schedule.mode or RecommendEmailScheduleMode.interval_from_now.value
    try:
        mode = RecommendEmailScheduleMode(mode_raw)
    except ValueError:
        logger.warning("未知定时模式，跳过 schedule_id=%s mode=%s", schedule.id, mode_raw)
        return False

    if mode == RecommendEmailScheduleMode.interval_from_now:
        if schedule.interval_hours <= 0:
            return False
        last_sent_at = _to_aware_utc(schedule.last_sent_at)
        if last_sent_at is None:
            return False
        elapsed_hours = (now_utc - last_sent_at).total_seconds() / 3600.0
        return elapsed_hours >= float(schedule.interval_hours)

    tz_name = schedule.timezone or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("无效时区，回退 UTC schedule_id=%s timezone=%s", schedule.id, tz_name)
        tz = timezone.utc

    now_local = now_utc.astimezone(tz)
    if now_local.hour != int(schedule.send_hour) or now_local.minute != int(schedule.send_minute):
        return False

    last_triggered_at = _to_aware_utc(schedule.last_triggered_at)
    if last_triggered_at and (now_utc - last_triggered_at).total_seconds() < 60:
        return False

    if mode == RecommendEmailScheduleMode.daily_fixed:
        last_sent_at = _to_aware_utc(schedule.last_sent_at)
        if last_sent_at is None:
            return True
        return last_sent_at.astimezone(tz).date() != now_local.date()

    if mode == RecommendEmailScheduleMode.interval_from_fixed:
        if schedule.interval_hours <= 0:
            return False
        last_sent_at = _to_aware_utc(schedule.last_sent_at)
        if last_sent_at is None:
            return True
        elapsed_hours = (now_utc - last_sent_at).total_seconds() / 3600.0
        return elapsed_hours >= float(schedule.interval_hours)

    return False


def _to_trend_object_for_fp(item) -> TrendObject:
    return TrendObject(
        title=item.title,
        summary=item.summary,
        tags=list(item.tags or []),
        audience=item.audience,
        product_opportunities=list(getattr(item, "product_opportunities", []) or []),
    )


def _filter_unsent_items(
    db: Session,
    *,
    schedule: MerchantHotspotRecommendEmailSchedule,
    brand_obj: BrandObject,
    items: list,
) -> tuple[list, str, list[tuple[str, str, str, float]]]:
    if not items:
        return [], "", []

    brand_fp = brand_fingerprint(brand_obj)
    item_meta: list[tuple] = []
    pairs: list[tuple[str, str]] = []
    for item in items:
        trend = item.trend
        trend_fp = trend_fingerprint(_to_trend_object_for_fp(trend))
        platform = (trend.platform or "youtube").strip().lower()
        item_meta.append((item, platform, trend_fp))
        pairs.append((brand_fp, trend_fp))

    existing = (
        db.query(MerchantHotspotRecommendEmailDelivery.brand_fp, MerchantHotspotRecommendEmailDelivery.trend_fp)
        .filter(MerchantHotspotRecommendEmailDelivery.merchant_id == schedule.merchant_id)
        .filter(tuple_(MerchantHotspotRecommendEmailDelivery.brand_fp, MerchantHotspotRecommendEmailDelivery.trend_fp).in_(pairs))
        .all()
    )
    sent_pairs = {(row[0], row[1]) for row in existing}

    unsent_items = []
    delivery_rows: list[tuple[str, str, str, float]] = []
    for item, platform, trend_fp in item_meta:
        pair = (brand_fp, trend_fp)
        if pair in sent_pairs:
            continue
        unsent_items.append(item)
        delivery_rows.append((platform, item.trend.id, trend_fp, float(item.match.compatibility_score)))
    return unsent_items, brand_fp, delivery_rows


async def _send_for_schedule(
    db: Session,
    schedule: MerchantHotspotRecommendEmailSchedule,
    now_utc: datetime,
) -> None:
    merchant = db.query(Merchant).filter(Merchant.id == schedule.merchant_id).first()
    if not merchant:
        logger.warning("定时发送配置缺少商户，跳过 schedule_id=%s merchant_id=%s", schedule.id, schedule.merchant_id)
        return

    brand = db.query(Brand).filter(Brand.merchant_id == schedule.merchant_id).first()
    if not brand:
        logger.info("商户未配置品牌，跳过定时发送 merchant_id=%s", schedule.merchant_id)
        return

    min_score = float(schedule.min_compatibility_score or Decimal("40.0"))
    brand_obj = _build_brand_object(brand)
    items, analyzed_count = await build_recommended_hotspots(
        platforms=_RECOMMEND_PLATFORMS,
        min_compatibility_score=min_score,
        brand=brand_obj,
    )
    unsent_items, brand_fp, delivery_rows = _filter_unsent_items(
        db,
        schedule=schedule,
        brand_obj=brand_obj,
        items=items,
    )
    if not unsent_items:
        schedule.last_triggered_at = now_utc
        db.commit()
        logger.info("定时推荐无新增可发热点 merchant_id=%s schedule_id=%s", schedule.merchant_id, schedule.id)
        return

    ok = send_recommendation_email(
        merchant_email=merchant.email,
        merchant_name=merchant.name,
        items=unsent_items,
        analyzed_count=analyzed_count,
        min_compatibility_score=min_score,
    )
    if ok:
        schedule.last_sent_at = now_utc
        schedule.last_triggered_at = now_utc
        for platform, trend_id, trend_fp, score in delivery_rows:
            db.add(
                MerchantHotspotRecommendEmailDelivery(
                    merchant_id=schedule.merchant_id,
                    schedule_id=schedule.id,
                    platform=platform,
                    trend_id=trend_id,
                    brand_fp=brand_fp,
                    trend_fp=trend_fp,
                    compatibility_score=Decimal(str(round(score, 2))),
                    min_score_at_send=Decimal(str(round(min_score, 2))),
                    sent_at=now_utc,
                    matched_at=now_utc,
                )
            )
        db.commit()
        logger.info("定时热点推荐邮件发送成功 merchant_id=%s schedule_id=%s", schedule.merchant_id, schedule.id)
    else:
        schedule.last_triggered_at = now_utc
        db.commit()
        logger.warning("定时热点推荐邮件发送失败 merchant_id=%s schedule_id=%s", schedule.merchant_id, schedule.id)


async def run_recommend_email_scheduler_once() -> None:
    now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    db = SessionLocal()
    try:
        schedules = (
            db.query(MerchantHotspotRecommendEmailSchedule)
            .filter(MerchantHotspotRecommendEmailSchedule.is_enabled.is_(True))
            .all()
        )
        for schedule in schedules:
            try:
                if not _should_send_now(schedule, now_utc):
                    continue
                await _send_for_schedule(db, schedule, now_utc)
            except Exception:
                logger.exception("处理定时热点推荐邮件失败 schedule_id=%s", schedule.id)
                db.rollback()
    finally:
        db.close()


def create_recommend_email_scheduler() -> AsyncIOScheduler | None:
    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        logger.info("邮件功能未启用，跳过热点推荐邮件定时器启动")
        return None

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_recommend_email_scheduler_once,
        trigger=IntervalTrigger(minutes=1, timezone="UTC"),
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler

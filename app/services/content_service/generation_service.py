"""
内容生成服务：视频(SeedDance)、图片(SeedDance)、文字(LLM)。
结合热点 TrendObject 与品牌 BrandObject，生成并落库。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Generation
from app.schemas.content import ProductObject
from app.schemas.hotspot import TrendObject, BrandObject
from app.services.merchant_service import get_brand_by_merchant_id
from app.services.content_service.prompt_templates import (
    build_video_prompt,
    build_image_prompt,
    build_text_prompt,
)
from app.services import seedance_client
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings=get_settings()

def get_brand_for_store(
    db: Session,
    merchant_id: int,
) -> Optional[BrandObject]:
    """从数据库获取商户品牌信息，转为 BrandObject。未设置品牌时返回 None。"""
    brand = get_brand_by_merchant_id(db, merchant_id)
    if not brand:
        return None
    return BrandObject(
        name=brand.name,
        core_value=brand.core_value or None,
        industry=brand.industry,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else None,
    )


def _trend_snapshot(trend: TrendObject) -> dict:
    return trend.model_dump()


def _brand_snapshot(brand: BrandObject) -> dict:
    return brand.model_dump()


# --------------------------
# 视频生成（异步 SeedDance）
# --------------------------
async def create_video_generation(
    db: Session,
    shopify_store_id: str,
    trend: TrendObject,
    brand: BrandObject,
    product: Optional[ProductObject] = None,
    *,
    user_prompt: Optional[str] = None,
    model: str = "doubao-seedance-1-5-pro",
    generation_type: str = "text_to_video",
    image_url: Optional[str] = None,
    aspect_ratio: str = "16:9",
    duration: float = 5,
    resolution: str = "720p",
) -> Generation:
    """
    创建视频生成任务：写入 Generation 记录，调用 SeedDance 发起任务，更新 external_id。
    """
    prompt_used = build_video_prompt(trend, brand, product, user_prompt, image_url)
    gen = Generation(
        shopify_store_id=shopify_store_id,
        type="video",
        status="pending",
        prompt_used=prompt_used,
        trend_snapshot=_trend_snapshot(trend),
        brand_snapshot=_brand_snapshot(brand),
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    try:
        data = await seedance_client.start_video_generation(
            prompt_used,
            model=model,
            generation_type=generation_type,
            image_url=image_url,
            aspect_ratio=aspect_ratio,
            duration=duration,
            resolution=resolution,
        )
        video_id = data.get("video_id")
        if video_id:
            gen.external_id = str(video_id)
            gen.status = "processing"
        else:
            gen.status = "failed"
            gen.error_message = "SeedDance 未返回 video_id"
    except Exception as e:
        logger.exception("SeedDance video start failed")
        gen.status = "failed"
        gen.error_message = str(e)

    db.commit()
    db.refresh(gen)
    return gen


async def refresh_video_status(db: Session, gen: Generation) -> None:
    """
    若 generation 为 video 且 status 为 pending/processing，向 SeedDance 拉取最新状态并更新 DB。
    """
    if gen.type != "video" or not gen.external_id:
        return
    if gen.status not in ("pending", "processing"):
        return
    try:
        data = await seedance_client.get_video_status(gen.external_id)
        status = (data.get("status") or "").lower()
        if status == "completed":
            gen.status = "completed"
            gen.result_url = data.get("video_url")
        elif status == "failed":
            gen.status = "failed"
            gen.error_message = data.get("error") or "Upstream failed"
        else:
            gen.status = "processing"
    except Exception as e:
        logger.warning("SeedDance get_video_status failed: %s", e)
        gen.error_message = str(e)
    db.commit()
    db.refresh(gen)


# --------------------------
# 图片生成（异步 SeedDance）
# --------------------------
async def create_image_generation(
    db: Session,
    shopify_store_id: str,
    trend: TrendObject,
    brand: BrandObject,
    *,
    user_prompt: Optional[str] = None,
    model: str = "seedream-4.5",
    resolution: str = "2k",
    aspect_ratio: str = "1:1",
    output_format: str = "png",
    reference_image_urls: Optional[list[str]] = None,
) -> Generation:
    """
    创建图片生成任务。若返回中含 image_url 则直接 completed；否则保留 pending/processing。
    """
    prompt_used = build_image_prompt(trend, brand, user_prompt)
    gen = Generation(
        shopify_store_id=shopify_store_id,
        type="image",
        status="pending",
        prompt_used=prompt_used,
        trend_snapshot=_trend_snapshot(trend),
        brand_snapshot=_brand_snapshot(brand),
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    try:
        data = await seedance_client.start_image_generation(
            prompt_used,
            model=model,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            output_format=output_format,
            reference_image_urls=reference_image_urls,
        )
        image_url = data.get("image_url") or data.get("url")
        if image_url:
            gen.status = "completed"
            gen.result_url = image_url
        else:
            ext_id = data.get("task_id") or data.get("id")
            if ext_id:
                gen.external_id = str(ext_id)
                gen.status = "processing"
    except Exception as e:
        logger.exception("SeedDance image start failed")
        gen.status = "failed"
        gen.error_message = str(e)

    db.commit()
    db.refresh(gen)
    return gen


# --------------------------
# 文字生成（大模型）
# --------------------------
def _call_llm_for_text(prompt: str) -> str:
    """调用 OpenAI 兼容接口生成文案。"""
    import os
    from openai import OpenAI

    api_key = settings.LLM_API_KEY or ""
    base_url = settings.LLM_API_URL
    if not api_key:
        raise ValueError("未配置 LLM_API_KEY，无法生成文字")
    client = OpenAI(api_key=api_key, base_url=base_url)
    model = settings.LLM_MODEL
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    if not content:
        raise ValueError("大模型未返回文案内容")
    return content.strip()


def create_text_generation(
    db: Session,
    shopify_store_id: str,
    trend: TrendObject,
    brand: BrandObject,
    *,
    user_prompt: Optional[str] = None,
    title: Optional[str] = None,
) -> Generation:
    """
    同步生成营销文案：调用 LLM，写入 Generation（type=text），结果存 result_text。
    """
    prompt_used = build_text_prompt(trend, brand, user_prompt)
    gen = Generation(
        shopify_store_id=shopify_store_id,
        type="text",
        status="pending",
        prompt_used=prompt_used,
        trend_snapshot=_trend_snapshot(trend),
        brand_snapshot=_brand_snapshot(brand),
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    try:
        result_text = _call_llm_for_text(prompt_used)
        gen.status = "completed"
        gen.result_text = result_text
    except Exception as e:
        logger.exception("LLM text generation failed")
        gen.status = "failed"
        gen.error_message = str(e)

    db.commit()
    db.refresh(gen)
    return gen


# --------------------------
# 查询
# --------------------------
def get_generation_by_id(db: Session, generation_id: int, shopify_store_id: str) -> Optional[Generation]:
    return (
        db.query(Generation)
        .filter(Generation.id == generation_id, Generation.shopify_store_id == shopify_store_id)
        .first()
    )


def list_generations(
    db: Session,
    shopify_store_id: str,
    type_filter: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
):
    q = db.query(Generation).filter(Generation.shopify_store_id == shopify_store_id)
    if type_filter:
        q = q.filter(Generation.type == type_filter)
    return q.order_by(Generation.id.desc()).offset(skip).limit(limit).all()


def create_deprecated_text_record(
    db: Session,
    shopify_store_id: str,
    title: str,
    prompt: str,
    generated_text: str,
) -> Generation:
    """旧版 /content/generate 占位：只写一条 type=text 的 Generation，无热点/品牌快照。"""
    gen = Generation(
        shopify_store_id=shopify_store_id,
        type="text",
        status="completed",
        prompt_used=prompt,
        trend_snapshot=None,
        brand_snapshot=None,
        result_text=generated_text,
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)
    return gen

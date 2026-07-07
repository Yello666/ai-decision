"""Product Select API 编排服务。

路由层只负责接收参数与返回响应；这里承载下载、识图、写库、聚合、供应链测试等业务编排。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from app.schemas.product_select import (
    InstagramRunRequest,
    MonitorCreateRequest,
    MonitorRunRequest,
    MonitorUpdateRequest,
    ObjectProfileCreateRequest,
    ObjectProfileUpdateRequest,
)
from app.services.productselect_service import config
from app.services.productselect_service.image_recognition import recognize_images
from app.services.productselect_service.instagram_apify import (
    InstagramPost,
    fetch_latest_posts,
)
from app.services.productselect_service.image_crop import crop_by_norm_box
from app.services.productselect_service.lens_filter import build_top_matches
from app.services.productselect_service.oss_uploader import sign_key, upload_bytes, upload_file
from app.services.productselect_service.repository import (
    create_image,
    create_matches_from_lens,
    create_object,
    create_object_profile,
    deactivate_objects_for_content,
    delete_matches_for_object,
    disable_monitor,
    get_active_object_profile,
    get_image,
    get_content,
    get_monitor,
    get_object,
    list_matches,
    list_monitors,
    list_objects,
    next_object_version_for_content,
    update_monitor,
    update_object_profile,
    upsert_content,
    upsert_monitor,
)
from app.services.productselect_service.serpapi_lens import search_by_image_url

logger = logging.getLogger(__name__)


def _safe_name(value: str) -> str:
    return value.strip().lstrip("@").replace("/", "_").replace(":", "_")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None, None


def _normalized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 - x1 <= 0.01 or y2 - y1 <= 0.01:
        return None
    return [x1, y1, x2, y2]


def _purge_local_if_uploaded(path: Path | str | None, *, oss_key: str | None) -> bool:
    """OSS 已上传且线上模式时删除本地文件。"""
    if not config.delete_local_after_oss_upload():
        return False
    if not oss_key:
        return False
    file_path = Path(path) if path else None
    if file_path is None or not file_path.is_file():
        return False
    try:
        file_path.unlink()
        logger.info("已删除本地文件 path=%s", file_path)
        return True
    except Exception:
        logger.exception("删除本地文件失败 path=%s", file_path)
        return False


def _finalize_image_local_ref(row, path: Path | str | None, *, oss_key: str | None) -> None:
    """线上模式且 OSS 已有备份时，删除本地文件并清空 local_path。"""
    if row is None:
        return
    effective_key = oss_key or getattr(row, "oss_key", None)
    if _purge_local_if_uploaded(path, oss_key=effective_key):
        row.local_path = None
    elif config.delete_local_after_oss_upload() and effective_key:
        row.local_path = None


def _signed_url_from_image_row(row) -> str | None:
    """按 oss_key 签发前端/API 可访问 URL；原图可回退 Instagram source_url。"""
    if row is None:
        return None
    if row.oss_key:
        try:
            return sign_key(row.oss_key, config.OSS_API_SIGN_URL_EXPIRE)
        except Exception:
            logger.exception("OSS 签名失败 key=%s", row.oss_key)
    if row.image_type == "source" and row.source_url:
        return row.source_url
    return None


def _ensure_image_on_oss(
    db: Session,
    row,
    *,
    prefix: str,
) -> str | None:
    """确保图片在 OSS 上有 oss_key，返回可用于 Lens 的签名 URL。"""
    if row is None:
        return None
    if row.oss_key:
        try:
            return sign_key(row.oss_key, config.OSS_SIGN_URL_EXPIRE)
        except Exception:
            logger.exception("OSS 签名失败 key=%s", row.oss_key)
    local_path = Path(row.local_path) if row.local_path else None
    if local_path is None or not local_path.exists():
        return _signed_url_from_image_row(row)
    try:
        uploaded = upload_file(local_path, prefix=prefix, expire_seconds=config.OSS_SIGN_URL_EXPIRE)
        row.oss_key = uploaded.key
        row.oss_url = uploaded.url
        _finalize_image_local_ref(row, local_path, oss_key=uploaded.key)
        db.commit()
        return uploaded.url
    except Exception:
        logger.exception("图片上传 OSS 失败 path=%s", local_path)
        if row.image_type == "source" and row.source_url:
            return row.source_url
        return None


def _upload_source_image(
    db: Session,
    *,
    content_id: int,
    image_path: Path,
    source_url: str,
) -> Any:
    width, height = _image_size(image_path)
    oss_key: str | None = None
    try:
        uploaded = upload_file(image_path, prefix=config.OSS_SOURCE_PREFIX)
        oss_key = uploaded.key
    except Exception:
        logger.exception("原图上传 OSS 失败 path=%s", image_path)
    return create_image(
        db,
        content_id=content_id,
        image_type="source",
        local_path=str(image_path),
        oss_key=oss_key,
        source_url=source_url,
        width=width,
        height=height,
    )


def _upload_recognition_artifact(
    *,
    content_id: int,
    recognition_version: int,
    payload: dict[str, Any],
) -> str | None:
    """将识图 JSON 备份到 OSS，返回 oss_key。"""
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        uploaded = upload_bytes(
            data,
            suffix=f"_v{recognition_version}.json",
            prefix=f"{config.OSS_RECOGNITION_PREFIX}{content_id}/",
        )
        return uploaded.key
    except Exception:
        logger.exception("识图 JSON 上传 OSS 失败 content_id=%s", content_id)
        return None


def _attach_crop_image(
    db: Session,
    *,
    obj,
    source_image,
    bbox: list[float] | None,
) -> None:
    if not bbox or source_image is None:
        return
    source_path = Path(source_image.local_path) if source_image.local_path else None
    if source_path is None or not source_path.exists():
        return
    crop_path = config.CROP_OUTPUT_DIR / f"object_{obj.id}" / "crop.jpg"
    saved = crop_by_norm_box(source_path, bbox, crop_path)
    if not saved:
        return
    oss_key: str | None = None
    try:
        uploaded = upload_file(saved, prefix=config.OSS_CROP_PREFIX)
        oss_key = uploaded.key
    except Exception:
        logger.exception("裁剪图上传 OSS 失败 object_id=%s", obj.id)
    width, height = _image_size(saved)
    crop_row = create_image(
        db,
        content_id=obj.content_id,
        image_type="crop",
        local_path=str(saved),
        oss_key=oss_key,
        width=width,
        height=height,
    )
    obj.crop_image_id = crop_row.id
    _finalize_image_local_ref(crop_row, saved, oss_key=oss_key)
    db.commit()


def _cleanup_post_local_files(
    *,
    images: list[tuple[Path, str]],
    image_rows: dict[str, Any],
    recognition_path: Path,
    recognition_oss_key: str | None,
) -> str | None:
    """整条帖子处理完成后清理本地原图与识图 JSON，返回应写入 content.raw_path 的值。"""
    for image_path, _ in images:
        image_row = image_rows.get(image_path.name)
        if image_row is None:
            continue
        _finalize_image_local_ref(image_row, image_path, oss_key=image_row.oss_key)

    if recognition_oss_key and config.delete_local_after_oss_upload():
        _purge_local_if_uploaded(recognition_path, oss_key=recognition_oss_key)
        return None
    return str(recognition_path)


_VALID_WEIGHT_UNITS = frozenset({"g", "kg", "lb", "oz"})


def _estimate_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _recognition_estimate_to_profile_kwargs(obj: dict[str, Any]) -> dict[str, Any] | None:
    """从识图 JSON 的 estimate 字段解析商品预估参数。"""
    raw = obj.get("estimate")
    if not isinstance(raw, dict):
        return None

    weight_unit = raw.get("weight_unit")
    if weight_unit is not None:
        weight_unit = str(weight_unit).strip().lower()
        if weight_unit not in _VALID_WEIGHT_UNITS:
            weight_unit = None

    weight_value = _estimate_float(raw.get("weight_value"))
    if weight_value is None:
        weight_unit = None

    currency = raw.get("currency")
    currency = str(currency).strip().upper() if currency else "USD"

    notes = raw.get("notes")
    notes = str(notes).strip() if notes else None

    kwargs = {
        "cost_price_min": _estimate_float(raw.get("cost_price_min")),
        "cost_price_max": _estimate_float(raw.get("cost_price_max")),
        "selling_price_min": _estimate_float(raw.get("selling_price_min")),
        "selling_price_max": _estimate_float(raw.get("selling_price_max")),
        "currency": currency or "USD",
        "length_cm": _estimate_float(raw.get("length_cm")),
        "width_cm": _estimate_float(raw.get("width_cm")),
        "height_cm": _estimate_float(raw.get("height_cm")),
        "volume_cm3": _estimate_float(raw.get("volume_cm3")),
        "weight_value": weight_value,
        "weight_unit": weight_unit,
        "source": "ai",
        "status": "draft",
        "notes": notes,
    }

    has_data = any(
        kwargs[key] is not None
        for key in (
            "cost_price_min",
            "cost_price_max",
            "selling_price_min",
            "selling_price_max",
            "length_cm",
            "width_cm",
            "height_cm",
            "volume_cm3",
            "weight_value",
            "notes",
        )
    )
    return kwargs if has_data else None


def _attach_profile_from_recognition(
    db: Session,
    *,
    object_row,
    obj: dict[str, Any],
) -> None:
    """将识图结果中的 estimate 写入 product_select_object_profiles。"""
    profile_kwargs = _recognition_estimate_to_profile_kwargs(obj)
    if profile_kwargs is None:
        return
    create_object_profile(
        db,
        object_id=object_row.id,
        deactivate_existing=True,
        **profile_kwargs,
    )


def _download_instagram_images(
    post: InstagramPost,
    out_dir: Path,
    *,
    max_images: int,
) -> list[tuple[Path, str]]:
    """下载 Instagram 帖子图片，返回 (local_path, source_url)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    urls = post.image_urls[:max_images]
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            out_path = out_dir / f"{post.post_id}_{idx:02d}.jpg"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                saved.append((out_path, url))
            except Exception:
                logger.exception("Instagram 图片下载失败 post=%s url=%s", post.post_id, url)
    return saved


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def profile_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "object_id": row.object_id,
        "cost_price_min": _decimal_to_float(row.cost_price_min),
        "cost_price_max": _decimal_to_float(row.cost_price_max),
        "selling_price_min": _decimal_to_float(row.selling_price_min),
        "selling_price_max": _decimal_to_float(row.selling_price_max),
        "currency": row.currency,
        "length_cm": _decimal_to_float(row.length_cm),
        "width_cm": _decimal_to_float(row.width_cm),
        "height_cm": _decimal_to_float(row.height_cm),
        "volume_cm3": _decimal_to_float(row.volume_cm3),
        "weight_value": _decimal_to_float(row.weight_value),
        "weight_unit": row.weight_unit,
        "source": row.source,
        "status": row.status,
        "reference_match_id": row.reference_match_id,
        "notes": row.notes,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def object_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "content_id": row.content_id,
        "source_image_id": row.source_image_id,
        "crop_image_id": row.crop_image_id,
        "category": row.category,
        "related_ip": row.related_ip,
        "description": row.description,
        "attributes": row.attributes_json,
        "ecommerce_potential": row.ecommerce_potential,
        "reason": row.reason,
        "bbox": row.bbox_json,
        "recognition_version": row.recognition_version,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def match_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "object_id": row.object_id,
        "source": row.source,
        "match_level": row.match_level,
        "title": row.title,
        "store": row.store,
        "url": row.url,
        "price": float(row.price) if row.price is not None else None,
        "currency": row.currency,
        "rating": float(row.rating) if row.rating is not None else None,
        "reviews": row.reviews,
        "in_stock": row.in_stock,
        "thumbnail_url": row.thumbnail_url,
        "raw_json": row.raw_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def image_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "image_type": row.image_type,
        "local_path": row.local_path,
        "oss_key": row.oss_key,
        "oss_url": _signed_url_from_image_row(row),
        "source_url": row.source_url,
        "width": row.width,
        "height": row.height,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def content_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "monitor_id": row.monitor_id,
        "platform": row.platform,
        "external_id": row.external_id,
        "url": row.url,
        "caption_or_title": row.caption_or_title,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "status": row.status,
        "raw_path": row.raw_path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def monitor_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "handle": row.handle,
        "display_name": row.display_name,
        "monitor_type": row.monitor_type,
        "score": float(row.score) if row.score is not None else 5.0,
        "is_enabled": row.is_enabled,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def object_detail_to_dict(db: Session, row) -> dict[str, Any]:
    item = object_to_dict(row)
    content = get_content(db, row.content_id) if row.content_id else None
    monitor = get_monitor(db, content.monitor_id) if content and content.monitor_id else None
    source_image = get_image(db, row.source_image_id) if row.source_image_id else None
    crop_image = get_image(db, row.crop_image_id) if row.crop_image_id else None
    item["source_content"] = content_to_dict(content)
    item["source_monitor"] = monitor_to_dict(monitor) if monitor else None
    item["source_image"] = image_to_dict(source_image)
    item["crop_image"] = image_to_dict(crop_image)
    item["is_test"] = row.content_id is None
    active_profile = get_active_object_profile(db, row.id)
    item["profile"] = profile_to_dict(active_profile) if active_profile else None
    return item


def create_monitor(payload: MonitorCreateRequest, db: Session) -> dict[str, Any]:
    row = upsert_monitor(
        db,
        platform=payload.platform,
        handle=payload.handle.strip().lstrip("@"),
        display_name=payload.display_name or config.display_name(payload.handle),
        monitor_type=payload.monitor_type,
        score=payload.score,
        is_enabled=payload.is_enabled,
    )
    return monitor_to_dict(row)


def update_monitor_by_id(
    monitor_id: int,
    payload: MonitorUpdateRequest,
    db: Session,
) -> dict[str, Any] | None:
    row = update_monitor(
        db,
        monitor_id,
        display_name=payload.display_name,
        score=payload.score,
        is_enabled=payload.is_enabled,
    )
    return monitor_to_dict(row) if row else None


def disable_monitor_by_id(monitor_id: int, db: Session) -> dict[str, Any] | None:
    row = disable_monitor(db, monitor_id)
    return monitor_to_dict(row) if row else None


def query_monitors(
    db: Session,
    *,
    platform: str | None,
    is_enabled: bool | None,
    limit: int,
) -> dict[str, Any]:
    rows = list_monitors(
        db,
        platform=platform,
        is_enabled=is_enabled,
        limit=limit,
    )
    return {
        "items": [monitor_to_dict(row) for row in rows],
        "returned_count": len(rows),
    }


def _find_instagram_profile_monitor(db: Session, handle: str):
    normalized = handle.strip().lstrip("@")
    rows = list_monitors(db, platform="instagram", is_enabled=None, limit=500)
    return next(
        (
            row
            for row in rows
            if row.handle == normalized and row.monitor_type == "profile"
        ),
        None,
    )


def run_instagram_monitor(payload: InstagramRunRequest, db: Session) -> dict[str, Any]:
    profiles = payload.profiles or config.INSTAGRAM_PROFILES
    fetched_posts = 0
    processed_posts = 0
    skipped_posts = 0
    failed_posts = 0
    object_total = 0

    for profile in profiles:
        monitor = _find_instagram_profile_monitor(db, profile)
        if monitor is None:
            monitor = upsert_monitor(
                db,
                platform="instagram",
                handle=profile.strip().lstrip("@"),
                display_name=config.display_name(profile),
                monitor_type="profile",
                score=5,
            )
        try:
            posts = fetch_latest_posts(profile, payload.posts_per_profile)
        except Exception:
            logger.exception("Instagram 抓取失败 profile=%s", profile)
            failed_posts += 1
            continue

        fetched_posts += len(posts)
        safe_user = _safe_name(profile)
        for post in posts:
            content = upsert_content(
                db,
                platform="instagram",
                external_id=post.post_id,
                monitor_id=monitor.id,
                url=post.url,
                caption_or_title=post.caption,
                published_at=_parse_datetime(post.timestamp),
                status="fetched",
            )
            post_dir = config.INSTAGRAM_OUTPUT_DIR / safe_user / post.post_id

            images = _download_instagram_images(
                post,
                post_dir,
                max_images=payload.max_images_per_post,
            )
            if not images:
                content.status = "failed"
                db.commit()
                failed_posts += 1
                continue

            try:
                image_paths = [path for path, _ in images]
                image_rows = {}
                for image_path, source_url in images:
                    image_row = _upload_source_image(
                        db,
                        content_id=content.id,
                        image_path=image_path,
                        source_url=source_url,
                    )
                    image_rows[image_path.name] = image_row

                result = recognize_images(image_paths, known_ip=config.display_name(post.username))
            except Exception:
                logger.exception("Instagram 识图失败 post=%s", post.post_id)
                content.status = "failed"
                db.commit()
                failed_posts += 1
                continue

            recognition_version = next_object_version_for_content(db, content.id)
            recognition_path = post_dir / f"recognition_v{recognition_version}.json"
            result["post_id"] = post.post_id
            result["username"] = post.username
            result["post_url"] = post.url
            result["timestamp"] = post.timestamp
            result["caption"] = post.caption
            recognition_oss_key = _upload_recognition_artifact(
                content_id=content.id,
                recognition_version=recognition_version,
                payload=result,
            )
            if recognition_oss_key:
                result["recognition_oss_key"] = recognition_oss_key
            recognition_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            deactivate_objects_for_content(db, content.id)
            for obj in result.get("objects") or []:
                if not isinstance(obj, dict):
                    continue
                source_names = obj.get("source_images") if isinstance(obj.get("source_images"), list) else []
                source_image = image_rows.get(source_names[0]) if source_names else None
                bbox = _normalized_bbox(obj.get("bbox"))
                object_row = create_object(
                    db,
                    content_id=content.id,
                    source_image_id=source_image.id if source_image else None,
                    category=obj.get("category") or "",
                    related_ip=obj.get("related_ip"),
                    description=obj.get("description"),
                    attributes=obj.get("attributes") if isinstance(obj.get("attributes"), list) else None,
                    ecommerce_potential=obj.get("ecommerce_potential") or "medium",
                    reason=obj.get("reason"),
                    bbox=bbox,
                    recognition_version=recognition_version,
                    is_active=True,
                    token_usage=result.get("token_usage") if isinstance(result.get("token_usage"), dict) else None,
                )
                _attach_crop_image(db, obj=object_row, source_image=source_image, bbox=bbox)
                _attach_profile_from_recognition(db, object_row=object_row, obj=obj)

            content.raw_path = _cleanup_post_local_files(
                images=images,
                image_rows=image_rows,
                recognition_path=recognition_path,
                recognition_oss_key=recognition_oss_key,
            )
            content.status = "recognized"
            db.commit()

            processed_posts += 1
            object_total += len(result.get("objects") or [])

    return {
        "profiles": profiles,
        "fetched_posts": fetched_posts,
        "processed_posts": processed_posts,
        "skipped_posts": skipped_posts,
        "failed_posts": failed_posts,
        "object_total": object_total,
        "output_dir": str(config.INSTAGRAM_OUTPUT_DIR),
    }


def run_monitors(payload: MonitorRunRequest, db: Session) -> dict[str, Any]:
    supported_profiles: list[str] = []
    unsupported_monitors: list[dict[str, Any]] = []

    for monitor_id in payload.monitor_ids:
        monitor = get_monitor(db, monitor_id)
        if monitor is None or not monitor.is_enabled:
            continue
        if monitor.platform == "instagram" and monitor.monitor_type == "profile":
            supported_profiles.append(monitor.handle)
        else:
            unsupported_monitors.append(monitor_to_dict(monitor))

    if supported_profiles:
        result = run_instagram_monitor(
            InstagramRunRequest(
                profiles=supported_profiles,
                posts_per_profile=payload.posts_per_profile,
                max_images_per_post=payload.max_images_per_post,
            ),
            db,
        )
    else:
        result = {
            "profiles": [],
            "fetched_posts": 0,
            "processed_posts": 0,
            "skipped_posts": 0,
            "failed_posts": 0,
            "object_total": 0,
            "output_dir": str(config.INSTAGRAM_OUTPUT_DIR),
        }

    result["unsupported_monitors"] = unsupported_monitors
    return result


def delete_object_by_id(db: Session, object_id: int) -> dict[str, Any] | None:
    obj = get_object(db, object_id)
    if obj is None:
        return None
    data = object_detail_to_dict(db, obj)
    delete_matches_for_object(db, object_id, commit=False)
    db.delete(obj)
    db.commit()
    return data


def refresh_object_matches(
    db: Session,
    *,
    object_id: int,
    lens_type: str,
    limit: int,
) -> dict[str, Any] | None:
    """刷新单个商品机会的商品匹配，会调用 OSS + SerpApi 并写入数据库。

    优先使用已有 crop 图；没有 crop 但有 source+bbox 时现场裁剪；
    如果没有 bbox，则退化为直接用 source 图跑 Lens。
    """
    obj = get_object(db, object_id)
    if obj is None:
        return None

    delete_matches_for_object(db, object_id, source="google_lens")

    image_url: str | None = None
    crop_image = get_image(db, obj.crop_image_id) if obj.crop_image_id else None
    source_image = get_image(db, obj.source_image_id) if obj.source_image_id else None

    # 1) 优先用裁剪图（oss_key 可跨机器；本地仅作处理缓存）
    if crop_image is not None:
        image_url = _ensure_image_on_oss(db, crop_image, prefix=config.OSS_CROP_PREFIX)

    # 2) 无裁剪图但有 source + bbox：现场裁剪并上传
    if image_url is None and source_image is not None:
        source_path = Path(source_image.local_path) if source_image.local_path else None
        if source_path and source_path.exists() and isinstance(obj.bbox_json, list) and len(obj.bbox_json) == 4:
            crop_path = config.CROP_OUTPUT_DIR / f"object_{obj.id}" / "crop.jpg"
            saved = crop_by_norm_box(source_path, obj.bbox_json, crop_path)
            if saved:
                oss_key: str | None = None
                lens_url: str | None = None
                try:
                    uploaded = upload_file(saved, prefix=config.OSS_CROP_PREFIX)
                    oss_key = uploaded.key
                    lens_url = uploaded.url
                except Exception:
                    logger.exception("现场裁剪图上传 OSS 失败 object_id=%s", obj.id)
                width, height = _image_size(saved)
                crop_row = create_image(
                    db,
                    content_id=obj.content_id,
                    image_type="crop",
                    local_path=str(saved),
                    oss_key=oss_key,
                    width=width,
                    height=height,
                )
                obj.crop_image_id = crop_row.id
                _finalize_image_local_ref(crop_row, saved, oss_key=oss_key)
                db.commit()
                image_url = lens_url

        # 3) 仍无裁剪图时，用原图兜底
        if image_url is None:
            image_url = _ensure_image_on_oss(db, source_image, prefix=config.OSS_SOURCE_PREFIX)

    if not image_url:
        raise ValueError("object 缺少可用于商品匹配的图片")

    lens = search_by_image_url(image_url, lens_type=lens_type)
    create_matches_from_lens(db, object_id=obj.id, lens_response=lens)
    top_matches = build_top_matches(
        lens,
        category=obj.category,
        related_ip=obj.related_ip,
        attributes=obj.attributes_json if isinstance(obj.attributes_json, list) else None,
        limit=limit,
    )
    return {
        "object": object_detail_to_dict(db, obj),
        "top_matches": top_matches,
        "matched_count": len(lens.get("visual_matches") or []),
        "from_cache": False,
    }


def query_objects(
    db: Session,
    *,
    potential: str | None,
    related_ip: str | None,
    category: str | None,
    include_test: bool,
    active_only: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    rows = list_objects(
        db,
        potential=potential,
        related_ip=related_ip,
        category=category,
        include_test=include_test,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(object_detail_to_dict(db, row))
    return {
        "items": items,
        "returned_count": len(rows),
    }


def query_matches(
    db: Session,
    *,
    object_id: int,
    source: str | None,
    limit: int,
) -> dict[str, Any]:
    rows = list_matches(db, object_id=object_id, source=source, limit=limit)
    return {
        "items": [match_to_dict(row) for row in rows],
        "returned_count": len(rows),
    }


def _validate_profile_reference(db: Session, object_id: int, reference_match_id: int | None) -> None:
    if reference_match_id is None:
        return
    from app.models.product_select import ProductSelectMatch

    match_row = db.query(ProductSelectMatch).filter(
        ProductSelectMatch.id == reference_match_id,
        ProductSelectMatch.object_id == object_id,
    ).first()
    if match_row is None:
        raise ValueError("reference_match_id 不存在或不属于该商品机会")


def get_object_profile_by_object_id(db: Session, object_id: int) -> dict[str, Any] | None:
    if get_object(db, object_id) is None:
        return None
    row = get_active_object_profile(db, object_id)
    return profile_to_dict(row) if row else None


def object_exists(db: Session, object_id: int) -> bool:
    return get_object(db, object_id) is not None


def upsert_object_profile(
    db: Session,
    object_id: int,
    payload: ObjectProfileCreateRequest,
) -> dict[str, Any] | None:
    if get_object(db, object_id) is None:
        return None
    _validate_profile_reference(db, object_id, payload.reference_match_id)
    row = create_object_profile(
        db,
        object_id=object_id,
        cost_price_min=payload.cost_price_min,
        cost_price_max=payload.cost_price_max,
        selling_price_min=payload.selling_price_min,
        selling_price_max=payload.selling_price_max,
        currency=payload.currency,
        length_cm=payload.length_cm,
        width_cm=payload.width_cm,
        height_cm=payload.height_cm,
        volume_cm3=payload.volume_cm3,
        weight_value=payload.weight_value,
        weight_unit=payload.weight_unit,
        source=payload.source,
        status=payload.status,
        reference_match_id=payload.reference_match_id,
        notes=payload.notes,
        is_active=True,
        deactivate_existing=True,
    )
    return profile_to_dict(row)


def patch_object_profile(
    db: Session,
    object_id: int,
    payload: ObjectProfileUpdateRequest,
) -> dict[str, Any] | None:
    if get_object(db, object_id) is None:
        return None
    row = get_active_object_profile(db, object_id)
    if row is None:
        return None
    if payload.reference_match_id is not None:
        _validate_profile_reference(db, object_id, payload.reference_match_id)
    updates = payload.model_dump(exclude_unset=True)
    updated = update_object_profile(db, row.id, **updates)
    return profile_to_dict(updated) if updated else None


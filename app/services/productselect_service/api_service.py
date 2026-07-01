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
    ProductMatchTestRequest,
)
from app.services.productselect_service import config
from app.services.productselect_service.image_recognition import recognize_images
from app.services.productselect_service.instagram_apify import (
    InstagramPost,
    fetch_latest_posts,
)
from app.services.productselect_service.image_crop import crop_by_norm_box
from app.services.productselect_service.lens_filter import build_top_matches
from app.services.productselect_service.oss_uploader import upload_and_sign
from app.services.productselect_service.repository import (
    clear_content_artifacts,
    create_image,
    create_matches_from_lens,
    create_object,
    delete_matches_for_object,
    disable_monitor,
    get_image,
    get_content,
    get_monitor,
    get_object,
    list_matches,
    list_monitors,
    list_objects,
    update_monitor,
    upsert_content,
    upsert_monitor,
)
from app.services.productselect_service.run_aggregate import (
    _write_outputs,
    aggregate,
)
from app.services.productselect_service.run_supply_test import process_image
from app.services.productselect_service.serpapi_lens import search_by_image_url

logger = logging.getLogger(__name__)


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else config.PROJECT_ROOT / p


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
        "oss_url": row.oss_url,
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
            recognition_path = post_dir / "recognition.json"
            if recognition_path.exists() and not payload.force:
                content.raw_path = str(recognition_path)
                content.status = "recognized"
                db.commit()
                skipped_posts += 1
                continue

            if payload.force:
                clear_content_artifacts(db, content.id)

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
                    width, height = _image_size(image_path)
                    image_row = create_image(
                        db,
                        content_id=content.id,
                        image_type="source",
                        local_path=str(image_path),
                        source_url=source_url,
                        width=width,
                        height=height,
                    )
                    image_rows[image_path.name] = image_row

                result = recognize_images(image_paths, known_ip=config.display_name(post.username))
            except Exception:
                logger.exception("Instagram 识图失败 post=%s", post.post_id)
                content.status = "failed"
                db.commit()
                failed_posts += 1
                continue

            result["post_id"] = post.post_id
            result["username"] = post.username
            result["post_url"] = post.url
            result["timestamp"] = post.timestamp
            result["caption"] = post.caption
            recognition_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            for obj in result.get("objects") or []:
                if not isinstance(obj, dict):
                    continue
                source_names = obj.get("source_images") if isinstance(obj.get("source_images"), list) else []
                source_image = image_rows.get(source_names[0]) if source_names else None
                create_object(
                    db,
                    content_id=content.id,
                    source_image_id=source_image.id if source_image else None,
                    category=obj.get("category") or "",
                    related_ip=obj.get("related_ip"),
                    description=obj.get("description"),
                    attributes=obj.get("attributes") if isinstance(obj.get("attributes"), list) else None,
                    ecommerce_potential=obj.get("ecommerce_potential") or "medium",
                    reason=obj.get("reason"),
                    token_usage=result.get("token_usage") if isinstance(result.get("token_usage"), dict) else None,
                )

            content.raw_path = str(recognition_path)
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
                force=payload.force,
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


def run_aggregate_to_files() -> dict[str, Any]:
    result = aggregate()
    if not result.get("rows") and not result.get("stats"):
        return {
            "summary_json": "",
            "summary_csv": "",
            "stats": {},
            "empty": True,
        }

    json_path, csv_path = _write_outputs(result)
    return {
        "summary_json": str(json_path),
        "summary_csv": str(csv_path),
        "stats": result.get("stats") or {},
        "empty": False,
    }


def read_summary(
    *,
    platform: str | None = None,
    account: str | None = None,
    potential: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    summary_path = config.PRODUCT_SELECT_DIR / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError("summary.json 不存在，请先调用 /product-select/aggregate/run")

    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = raw.get("rows") or []
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]
    if account:
        rows = [r for r in rows if r.get("account") == account]
    if potential:
        p = potential.strip().lower()
        rows = [r for r in rows if r.get("ecommerce_potential") == p]

    return {
        "stats": raw.get("stats") or {},
        "rows": rows[:limit],
        "returned_count": min(len(rows), limit),
    }


def _slim_supply_result(result: dict[str, Any]) -> dict[str, Any]:
    """接口返回精简版：保留所有物件，但每个物件仅返回 top_matches，不直接返回完整 lens。"""
    slim = dict(result)
    items: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        compact = {k: v for k, v in item.items() if k != "lens"}
        if not compact.get("top_matches") and isinstance(item.get("lens"), dict):
            compact["top_matches"] = build_top_matches(
                item["lens"],
                category=item.get("category"),
                related_ip=item.get("related_ip"),
                limit=3,
            )
        else:
            compact["top_matches"] = compact.get("top_matches") or []
        items.append(compact)
    slim["items"] = items
    return slim


def run_supply_test(payload: ProductMatchTestRequest) -> dict[str, Any]:
    config.SUPPLY_TEST_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failed_images = 0

    potential_filter = payload.potential_filter or []
    for raw_path in payload.images:
        image_path = resolve_path(raw_path)
        if not image_path.exists():
            failed_images += 1
            results.append({"source_image": str(image_path), "error": "image_not_found"})
            continue

        try:
            result = process_image(
                image_path,
                potential_filter=potential_filter,
                lens_type=payload.lens_type,
            )
        except Exception as exc:
            logger.exception("供应链测试失败 image=%s", image_path)
            failed_images += 1
            results.append({"source_image": str(image_path), "error": str(exc)})
            continue

        out_path = config.SUPPLY_TEST_DIR / f"{image_path.stem}.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result_path = str(out_path)

        slim_result = _slim_supply_result(result)
        slim_result["result_path"] = result_path
        results.append(slim_result)

    return {
        "processed_images": len(payload.images) - failed_images,
        "failed_images": failed_images,
        "results": results,
        "output_dir": str(config.SUPPLY_TEST_DIR),
    }


def read_supply_result(image_stem: str) -> dict[str, Any]:
    result_path = config.SUPPLY_TEST_DIR / f"{image_stem}.json"
    if not result_path.exists():
        raise FileNotFoundError("供应链测试结果不存在")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    slim = _slim_supply_result(result)
    slim["result_path"] = str(result_path)
    return slim


def get_object_matches(
    db: Session,
    *,
    object_id: int,
    limit: int,
) -> dict[str, Any] | None:
    """查看单个商品机会已有商品匹配，只读数据库，不调用外部服务。"""
    obj = get_object(db, object_id)
    if obj is None:
        return None
    cached = list_matches(db, object_id=object_id, source="google_lens", limit=limit)
    return {
        "object": object_detail_to_dict(db, obj),
        "top_matches": [match_to_dict(row) for row in cached],
        "matched_count": len(cached),
        "from_cache": True,
    }


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

    # 1) 已有裁剪图：优先重新签 OSS URL，避免旧签名过期
    if crop_image and crop_image.local_path and Path(crop_image.local_path).exists():
        image_url = upload_and_sign(Path(crop_image.local_path))
    elif crop_image and crop_image.oss_url:
        image_url = crop_image.oss_url

    # 2) 无裁剪图但有 source + bbox：现场裁剪并上传
    if image_url is None and source_image and source_image.local_path and Path(source_image.local_path).exists():
        source_path = Path(source_image.local_path)
        if isinstance(obj.bbox_json, list) and len(obj.bbox_json) == 4:
            crop_path = config.CROP_OUTPUT_DIR / f"object_{obj.id}" / "crop.jpg"
            saved = crop_by_norm_box(source_path, obj.bbox_json, crop_path)
            if saved:
                oss_url = upload_and_sign(saved)
                width, height = _image_size(saved)
                crop_row = create_image(
                    db,
                    content_id=obj.content_id,
                    image_type="crop",
                    local_path=str(saved),
                    oss_url=oss_url,
                    width=width,
                    height=height,
                )
                obj.crop_image_id = crop_row.id
                db.commit()
                image_url = oss_url

        # 3) 仍无裁剪图时，用原图兜底
        if image_url is None:
            image_url = upload_and_sign(source_path)

    # 4) 最后兜底：已有远程图
    if image_url is None and source_image:
        image_url = source_image.oss_url or source_image.source_url

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
    limit: int,
    offset: int,
) -> dict[str, Any]:
    rows = list_objects(
        db,
        potential=potential,
        related_ip=related_ip,
        category=category,
        include_test=include_test,
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


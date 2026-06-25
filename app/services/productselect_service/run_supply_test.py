"""供应链对齐测试入口：对几张图跑通「检测→裁剪→上传OSS→Google Lens」全链路。

流程：
  config.SUPPLY_TEST_IMAGES 里的每张图
    → qwen-vl-plus 检测商品 + bbox
    → Pillow 按 bbox 裁出每个商品小图（存 CROP_OUTPUT_DIR）
    → 上传 OSS 拿签名 URL
    → SerpApi Google Lens 搜同款 → 完整响应原样保存
  结果写到 config.SUPPLY_TEST_DIR 下，每张图一个 JSON。

运行（项目根目录 D:\\ai-decision 下）：
       python -m app.services.productselect_service.run_supply_test
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config
from .bbox_detect import detect_objects_with_boxes
from .image_crop import crop_by_norm_box
from .oss_uploader import upload_and_sign
from .serpapi_lens import search_by_image_url

logger = logging.getLogger("productselect.supply")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (config.PROJECT_ROOT / path)


def process_image(image_path: Path) -> dict:
    """对单张图跑完整链路，返回结构化结果。"""
    detected = detect_objects_with_boxes(image_path)

    # 按潜力过滤：只对配置允许的潜力等级查 Lens，控制 SerpApi 调用次数
    allowed = config.SUPPLY_POTENTIAL_FILTER
    if allowed:
        allowed_set = {s.strip().lower() for s in allowed}
        objects = [o for o in detected if o.get("ecommerce_potential") in allowed_set]
        logger.info(
            "潜力过滤(%s)：检测 %d 个 → 保留 %d 个查 Lens",
            "/".join(sorted(allowed_set)), len(detected), len(objects),
        )
    else:
        objects = detected

    crop_root = config.CROP_OUTPUT_DIR / image_path.stem
    results: list[dict] = []

    for idx, obj in enumerate(objects, start=1):
        item: dict = {
            "category": obj["category"],
            "related_ip": obj["related_ip"],
            "ecommerce_potential": obj["ecommerce_potential"],
            "box": obj["box"],
        }
        crop_path = crop_root / f"crop_{idx:02d}.jpg"
        saved = crop_by_norm_box(image_path, obj["box"], crop_path)
        if not saved:
            item["error"] = "crop_failed"
            results.append(item)
            continue
        item["crop_path"] = str(saved)

        try:
            oss_url = upload_and_sign(saved)
            item["oss_url"] = oss_url
        except Exception as exc:
            logger.exception("上传 OSS 失败：%s", saved)
            item["error"] = f"oss_failed: {exc}"
            results.append(item)
            continue

        try:
            item["lens"] = search_by_image_url(oss_url)
        except Exception as exc:
            logger.exception("Google Lens 调用失败：%s", oss_url)
            item["error"] = f"lens_failed: {exc}"

        results.append(item)

    return {
        "source_image": str(image_path),
        "detected_count": len(detected),
        "queried_count": len(objects),
        "items": results,
    }


def run() -> None:
    _setup_logging()
    logger.info("供应链测试开始，图片数：%d", len(config.SUPPLY_TEST_IMAGES))
    config.SUPPLY_TEST_DIR.mkdir(parents=True, exist_ok=True)

    for raw in config.SUPPLY_TEST_IMAGES:
        image_path = _resolve_path(raw)
        if not image_path.exists():
            logger.warning("图片不存在，跳过：%s", image_path)
            continue
        logger.info("处理图片：%s", image_path)
        try:
            result = process_image(image_path)
        except Exception:
            logger.exception("处理失败：%s", image_path)
            continue

        out_path = config.SUPPLY_TEST_DIR / f"{image_path.stem}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "已保存结果：%s（检测 %d / 查询 %d）",
            out_path, result["detected_count"], result["queried_count"],
        )

    logger.info("供应链测试完成，输出目录：%s", config.SUPPLY_TEST_DIR)


if __name__ == "__main__":
    run()

"""聚合看板：扫描 productSelect/ 下所有 recognition.json，汇总成一张按潜力排序的总表。

把分散在各帖子/视频目录里的识图结果，拍平成「一物件一行」的清单，方便一眼看清
全部产出、挑出高潜力物件去做下一步（供应链对齐）。

输出（写到 config.PRODUCT_SELECT_DIR 下）：
  - summary.json —— 含统计信息 + 全部物件行（给程序用）
  - summary.csv  —— 同样的物件行，Excel 可直接打开排序筛选（utf-8-sig，避免中文乱码）

运行方式（在项目根目录 D:\\ai-decision 下执行）：
       python -m app.services.productselect_service.run_aggregate
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger("productselect.aggregate")

# 潜力排序权重：high > medium > low > 其它
_POTENTIAL_RANK = {"high": 3, "medium": 2, "low": 1}

_CSV_FIELDS = [
    "platform",
    "account",
    "related_ip",
    "category",
    "ecommerce_potential",
    "description",
    "attributes",
    "reason",
    "source_url",
    "source_images",
    "content_id",
    "timestamp",
]


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _platform_account(path: Path) -> tuple[str, str]:
    """从 recognition.json 路径推断平台与账号/频道。

    路径形如 productSelect/<instagram_pic|youtube_pic>/<account>/<id>/recognition.json
    """
    try:
        rel = path.relative_to(config.PRODUCT_SELECT_DIR)
        pic_dir = rel.parts[0]
        account = rel.parts[1] if len(rel.parts) >= 2 else ""
    except ValueError:
        pic_dir = ""
        account = path.parent.parent.name

    if "instagram" in pic_dir:
        platform = "instagram"
    elif "youtube" in pic_dir:
        platform = "youtube"
    else:
        platform = pic_dir or "unknown"
    return platform, account


def _source_url(platform: str, data: dict[str, Any]) -> str:
    if platform == "instagram":
        return data.get("post_url") or ""
    if platform == "youtube":
        vid = data.get("video_id")
        return f"https://www.youtube.com/watch?v={vid}" if vid else ""
    return data.get("post_url") or ""


def _content_id(platform: str, data: dict[str, Any]) -> str:
    return str(data.get("post_id") or data.get("video_id") or "")


def aggregate() -> dict[str, Any]:
    """扫描并聚合所有 recognition.json，返回 {stats, rows}。"""
    root = config.PRODUCT_SELECT_DIR
    rows: list[dict[str, Any]] = []
    files_processed = 0
    files_empty = 0
    total_tokens = 0
    potential_counts: dict[str, int] = {}

    if not root.exists():
        logger.warning("产物目录不存在：%s（请先运行抓取/识图）", root)
        return {"stats": {}, "rows": []}

    for json_path in sorted(root.rglob("recognition.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("读取失败：%s", json_path)
            continue

        files_processed += 1
        usage = data.get("token_usage") or {}
        if isinstance(usage.get("total_tokens"), int):
            total_tokens += usage["total_tokens"]

        platform, account = _platform_account(json_path)
        source_url = _source_url(platform, data)
        content_id = _content_id(platform, data)
        timestamp = data.get("timestamp") or ""

        objects = data.get("objects") or []
        if not objects:
            files_empty += 1
            continue

        for obj in objects:
            if not isinstance(obj, dict):
                continue
            potential = (obj.get("ecommerce_potential") or "").strip().lower()
            potential_counts[potential or "unknown"] = potential_counts.get(potential or "unknown", 0) + 1
            rows.append(
                {
                    "platform": platform,
                    "account": account,
                    "related_ip": obj.get("related_ip") or "",
                    "category": obj.get("category") or "",
                    "ecommerce_potential": potential,
                    "description": obj.get("description") or "",
                    "attributes": "、".join(obj.get("attributes") or []),
                    "reason": obj.get("reason") or "",
                    "source_url": source_url,
                    "source_images": "、".join(obj.get("source_images") or []),
                    "content_id": content_id,
                    "timestamp": timestamp,
                }
            )

    # 按潜力从高到低排序，其次按平台、账号聚拢
    rows.sort(
        key=lambda r: (
            -_POTENTIAL_RANK.get(r["ecommerce_potential"], 0),
            r["platform"],
            r["account"],
        )
    )

    stats = {
        "files_processed": files_processed,
        "files_with_objects": files_processed - files_empty,
        "files_empty": files_empty,
        "object_total": len(rows),
        "potential_counts": potential_counts,
        "total_tokens": total_tokens,
    }
    return {"stats": stats, "rows": rows}


def _write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    config.PRODUCT_SELECT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = config.PRODUCT_SELECT_DIR / "summary.json"
    csv_path = config.PRODUCT_SELECT_DIR / "summary.csv"

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # utf-8-sig：带 BOM，Excel 打开中文不乱码
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow(row)

    return json_path, csv_path


def run() -> None:
    _setup_logging()
    logger.info("开始聚合，扫描目录：%s", config.PRODUCT_SELECT_DIR)
    result = aggregate()
    stats = result["stats"]
    if not result["rows"] and not stats:
        return

    json_path, csv_path = _write_outputs(result)
    logger.info(
        "聚合完成：识图文件 %s 个（有产出 %s / 空 %s），物件 %s 个，累计 token %s",
        stats.get("files_processed", 0),
        stats.get("files_with_objects", 0),
        stats.get("files_empty", 0),
        stats.get("object_total", 0),
        stats.get("total_tokens", 0),
    )
    logger.info("潜力分布：%s", stats.get("potential_counts", {}))
    logger.info("输出：%s ; %s", json_path, csv_path)


if __name__ == "__main__":
    run()

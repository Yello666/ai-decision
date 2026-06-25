"""把本地图片上传到阿里云 OSS（私有 Bucket），返回临时签名 URL 供 SerpApi 抓取。

复用项目 .env 的 AK/SK 与主项目的 Endpoint/Bucket（新加坡 region 需 AuthV4）。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import oss2

from . import config

logger = logging.getLogger(__name__)

_bucket: "oss2.Bucket | None" = None


def _get_bucket() -> oss2.Bucket:
    global _bucket
    if _bucket is not None:
        return _bucket
    cfg = config.get_oss_config()
    if not cfg.get("ak") or not cfg.get("sk"):
        raise RuntimeError("缺少 OSS 凭据：请在项目根 .env 配置 AK / SK。")
    # 新加坡 region 必须用 AuthV4（与 app/api/v1/upload.py 一致）
    auth = oss2.AuthV4(cfg["ak"], cfg["sk"])
    _bucket = oss2.Bucket(
        auth,
        cfg["endpoint"],
        cfg["bucket"],
        region=cfg["region"],
    )
    return _bucket


def upload_and_sign(local_path: Path, expire_seconds: int | None = None) -> str:
    """上传本地文件到 OSS，返回带签名的临时可访问 URL。"""
    bucket = _get_bucket()
    ext = local_path.suffix or ".jpg"
    key = f"{config.OSS_UPLOAD_PREFIX}{uuid.uuid4().hex}{ext}"
    bucket.put_object_from_file(key, str(local_path))
    expire = expire_seconds if expire_seconds is not None else config.OSS_SIGN_URL_EXPIRE
    url = bucket.sign_url("GET", key, expire)
    logger.info("已上传 OSS：%s → key=%s", local_path.name, key)
    return url

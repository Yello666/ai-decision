"""把本地文件上传到阿里云 OSS（私有 Bucket），并按 oss_key 按需签发访问 URL。

复用项目 .env 的 AK/SK 与主项目的 Endpoint/Bucket（新加坡 region 需 AuthV4）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import oss2

from . import config

logger = logging.getLogger(__name__)

_bucket: oss2.Bucket | None = None


@dataclass(frozen=True)
class OssUploadResult:
    key: str
    url: str


def _get_bucket() -> oss2.Bucket:
    global _bucket
    if _bucket is not None:
        return _bucket
    cfg = config.get_oss_config()
    if not cfg.get("ak") or not cfg.get("sk"):
        raise RuntimeError("缺少 OSS 凭据：请在项目根 .env 配置 AK / SK。")
    auth = oss2.AuthV4(cfg["ak"], cfg["sk"])
    _bucket = oss2.Bucket(
        auth,
        cfg["endpoint"],
        cfg["bucket"],
        region=cfg["region"],
    )
    return _bucket


def sign_key(key: str, expire_seconds: int | None = None) -> str:
    """对已有 OSS 对象 key 签发临时 GET URL。"""
    bucket = _get_bucket()
    expire = expire_seconds if expire_seconds is not None else config.OSS_API_SIGN_URL_EXPIRE
    return bucket.sign_url("GET", key, expire)


def upload_file(
    local_path: Path,
    *,
    prefix: str | None = None,
    expire_seconds: int | None = None,
) -> OssUploadResult:
    """上传本地文件到 OSS，返回 key 与签名 URL。"""
    bucket = _get_bucket()
    ext = local_path.suffix or ".jpg"
    upload_prefix = prefix if prefix is not None else config.OSS_CROP_PREFIX
    key = f"{upload_prefix}{uuid.uuid4().hex}{ext}"
    bucket.put_object_from_file(key, str(local_path))
    expire = expire_seconds if expire_seconds is not None else config.OSS_API_SIGN_URL_EXPIRE
    url = bucket.sign_url("GET", key, expire)
    logger.info("已上传 OSS：%s → key=%s", local_path.name, key)
    return OssUploadResult(key=key, url=url)


def upload_bytes(
    data: bytes,
    *,
    suffix: str,
    prefix: str | None = None,
    expire_seconds: int | None = None,
) -> OssUploadResult:
    """上传内存内容到 OSS。"""
    bucket = _get_bucket()
    upload_prefix = prefix if prefix is not None else config.OSS_RECOGNITION_PREFIX
    key = f"{upload_prefix}{uuid.uuid4().hex}{suffix}"
    bucket.put_object(key, data)
    expire = expire_seconds if expire_seconds is not None else config.OSS_API_SIGN_URL_EXPIRE
    url = bucket.sign_url("GET", key, expire)
    logger.info("已上传 OSS 字节流 → key=%s", key)
    return OssUploadResult(key=key, url=url)


def upload_and_sign(
    local_path: Path,
    *,
    prefix: str | None = None,
    expire_seconds: int | None = None,
) -> str:
    """兼容旧调用：上传并仅返回签名 URL。"""
    return upload_file(
        local_path,
        prefix=prefix,
        expire_seconds=expire_seconds,
    ).url

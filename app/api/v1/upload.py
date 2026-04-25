import os
import uuid
import logging
from datetime import datetime, timezone

import oss2
from fastapi import APIRouter, UploadFile, File, HTTPException

from app.core.config import get_settings
from app.core.responses import success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["Upload"])
UPLOAD_DIR = "uploads/"


def get_oss():
    settings = get_settings()
    # 新加坡必须用 V4
    auth = oss2.AuthV4(settings.AK, settings.SK)
    return oss2.Bucket(
        auth,
        settings.Endpoint,
        settings.Bucket,
        region="ap-southeast-1"
    )


@router.post("/file")
async def upload_file(file: UploadFile = File(...)):
    # settings = get_settings()
    ext = os.path.splitext(file.filename)[1]
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"{UPLOAD_DIR}{date}/{uuid.uuid4().hex}{ext}"

    try:
        bucket = get_oss()
        bucket.put_object(key, file.file)
    except Exception as e:
        logger.exception("上传失败")
        raise HTTPException(status_code=500, detail="上传失败")

    # 生成私有Bucket可访问的临时签名URL
    signed_url = bucket.sign_url('GET', key, 21600) # 6小时有效

    return success({
        "url": signed_url,   # 返回url，访问直接下载
        "key": key,          # 用于后续业务保存
    })
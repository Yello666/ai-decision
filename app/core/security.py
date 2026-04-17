from datetime import datetime, timedelta, timezone
import hashlib
import os
import uuid
from typing import Tuple

from jose import jwt

from .config import get_settings


def get_password_hash(password: str) -> str:
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return f"{salt}${hashed}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        # 1. Extract salt from the stored string
        salt, stored_hash = hashed_password.split('$')
        # 2. Hash the input password with the same salt
        hashed = hashlib.sha256((plain_password + salt).encode('utf-8')).hexdigest()
        # 3. Compare
        return hashed == stored_hash
    except ValueError:
        return False

def create_access_token(subject: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": subject,
        "exp": expire,
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> Tuple[str, str]:
    """生成 refresh token，返回 ``(token, jti)``。

    ``jti`` 用于在 Redis 中登记“当前有效的 refresh 会话”，支持轮换时失效旧 token。
    """
    settings = get_settings()
    jti = uuid.uuid4().hex
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": subject,
        "exp": expire,
        "type": "refresh",
        "jti": jti,
    }
    token = jwt.encode(to_encode, settings.JWT_REFRESH_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def decode_refresh_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.JWT_REFRESH_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def get_refresh_ttl_seconds() -> int:
    """返回 refresh token 的生存期（秒），用于 Redis 会话 TTL。"""
    settings = get_settings()
    return settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60



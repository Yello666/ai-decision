from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Decision Platform"
    API_V1_PREFIX: str = "/api/v1"

    MYSQL_HOST: str = "192.168.64.2"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DB: str = "shopify_ai"
    MYSQL_POOL_SIZE: int = 10
    MYSQL_MAX_OVERFLOW: int = 20

    REDIS_HOST: str = "192.168.64.2"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str ="123456"

    JWT_SECRET_KEY: str = "hupperhupperhupperhupperhupperhupper"
    JWT_REFRESH_SECRET_KEY: str = "hupperrefreshhupperrefreshhupperrefresh"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = "*"

    SHOPIFY_API_KEY: Optional[str] = "49455de72dcf6e22f078ee97e94667ef"
    SHOPIFY_API_SECRET: Optional[str] = "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"
    SHOPIFY_API_VERSION: str = "2026-01"
    SHOPIFY_REDIRECT_URI: Optional[str] = "http://localhost:8000/api/v1/auth/shopify/callback"

    AUTODL_SERVICE_URL: str = "http://localhost:8001/predict"
    
    # Test flags
    USE_SQLITE: bool = False
    USE_MOCK_REDIS: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    # 在受限环境（如沙盒）里读取 `.env` 可能触发 PermissionError，
    # 这里做一次降级：读不了就只用系统环境变量 + 默认值，保证应用可启动。
    try:
        return Settings()
    except PermissionError:
        return Settings(_env_file=None)
    except OSError:
        return Settings(_env_file=None)

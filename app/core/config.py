from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Decision Platform"
    API_V1_PREFIX: str = "/api/v1"

    # 本地环境
    # MYSQL_HOST: str = "192.168.64.2"
    # MYSQL_PORT: int = 3306
    # MYSQL_USER: str = "root"
    # MYSQL_PASSWORD: str = "123456"
    # MYSQL_DB: str = "shopify_ai"

    #云端环境
    MYSQL_HOST: str = "rm-t4nxkze6hcj074157.mysql.singapore.rds.aliyuncs.com"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "hupper"
    MYSQL_PASSWORD: str = "gogogoHupper666!"
    MYSQL_DB: str = "shopify_ai"

    MYSQL_POOL_SIZE: int = 10
    MYSQL_MAX_OVERFLOW: int = 20

    #本地环境
    # REDIS_HOST: str = "192.168.64.2"
    # REDIS_PORT: int = 6379
    # REDIS_DB: int = 0
    # REDIS_PASSWORD: str = "123456"

    # 云端环境
    REDIS_HOST: str = "r-t4noqzbz7kk4xrmee3.redis.singapore.rds.aliyuncs.com"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str ="gogogoHupper888!"


    JWT_SECRET_KEY: str = "hupperhupperhupperhupperhupperhupper"
    JWT_REFRESH_SECRET_KEY: str = "hupperrefreshhupperrefreshhupperrefresh"
    JWT_ALGORITHM: str = "HS256"
    # 登录30分钟后会过期
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    LOG_LEVEL: str = "INFO"


    # 允许的前端域名：在有cookie和Authorization请求头的时候需要具体域名，在这里配置即可。其余时候不会使用这里的域名
    #多个域名使用,分隔
    ALLOWED_ORIGINS: str = "https://shop-ai.cc,https://www.shop-ai.cc,http://127.0.0.1:8000"

    # shopifyAPP的client id,自己携带这个就可以被shopifyAPP认为是client，发起请求
    SHOPIFY_API_KEY: Optional[str] = "3e209878da5f4b10514b91b689c955c5"
    SHOPIFY_API_SECRET: Optional[str] = "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"
    SHOPIFY_API_VERSION: str = "2026-01"
    SHOPIFY_REDIRECT_URI: Optional[str] = "https://shop-ai.xin/api/v1/auth/shopify/callback"

    #本地开发
    # SHOPIFY_REDIRECT_URI: Optional[str] = "http://127.0.0.1:8000/api/v1/auth/shopify/callback"

    AUTODL_SERVICE_URL: str = "http://localhost:8001/predict"

    # SeedDance 2.0（字节跳动视频/图片生成）
    SEEDANCE_API_KEY: Optional[str] = "sk-sd_NEUL_FQXdIAmEmhD-yf8jIJdnHYZZtBqjpOeyCyE" # 从 .env 或环境变量读取，如 sk-sd_xxx
    SEEDANCE_BASE_URL: str = "https://seedance2.app/api/v1"
    # 图片生成模型：须与上游支持的 model 一致，否则会报 Invalid image model selected。可覆盖为 seedream-4.5 / seedream-4.0 等
    SEEDANCE_IMAGE_MODEL: str = "seedream-4.5"

    # 文本生成用大模型（与热点匹配共用）
    LLM_API_KEY: Optional[str] = "sk-b0fc3528ced64aa4b31eca19eb10fb39"
    LLM_API_URL: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL:Optional[str]="qwen3.5-plus"

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

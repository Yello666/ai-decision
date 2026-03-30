from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # 忽略 .env 中多余的配置项，防止报错
    )

    PROJECT_NAME: str = "AI Decision Platform"
    API_V1_PREFIX: str = "/api/v1"
    # 有默认值，容器会先读这个不会读.env
    # Database
    # MYSQL_HOST: str="192.168.64.2"
    # MYSQL_PASSWORD:str="123456"
    MYSQL_HOST: str
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD:str
    MYSQL_DB: str = "shopify_ai"

    MYSQL_POOL_SIZE: int = 10
    MYSQL_MAX_OVERFLOW: int = 20

    # Redis
    # REDIS_HOST: str="192.168.64.2"
    # REDIS_PASSWORD:str="123456"
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD:str

    # JWT
    JWT_SECRET_KEY: str
    JWT_REFRESH_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    # 登录30分钟后会过期
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 60 * 24 * 7

    LOG_LEVEL: str = "INFO"


    # 允许的前端域名：在有cookie和Authorization请求头的时候需要具体域名，在这里配置即可。其余时候不会使用这里的域名
    #多个域名使用,分隔
    ALLOWED_ORIGINS: str = "https://shop-ai.cc,https://www.shop-ai.cc,http://127.0.0.1:8000"

    # ShopifyhopifyAPP的client id,自己携带这个就可以被shopifyAPP认为是client，发起请求
    SHOPIFY_API_KEY: str
    SHOPIFY_API_SECRET: str
    SHOPIFY_API_VERSION: str = "2026-01"
    SHOPIFY_REDIRECT_URI: str = "http://127.0.0.1:8000/api/v1/auth/shopify/callback"

    AUTODL_SERVICE_URL: str = "http://localhost:8001/predict"

    # SeedDance 2.0
    SEEDANCE_API_KEY: Optional[str] = None
    SEEDANCE_BASE_URL: str = "https://seedance2.app/api/v1"
    SEEDANCE_IMAGE_MODEL: str = "seedream-4.5"

    # LLM (Qwen / DashScope)
    LLM_API_KEY: Optional[str] = None
    LLM_API_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL: str = "qwen3.5-plus"

    # Agent LLM (Volcengine / Doubao)
    VOLCENGINE_API_KEY: str
    AGENT_MODEL_NAME: str = "doubao-seed-1-8-251228"
    VOLCENGINE_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"

    # Search tools
    TAVILY_API_KEY: str
    SERPAPI_API_KEY: Optional[str] = None

    # Competitor cache
    COMPETITOR_CACHE_TTL: int = 7200

    # Test flags
    USE_SQLITE: bool = False
    USE_MOCK_REDIS: bool = False


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

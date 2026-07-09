from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略.env中多余的配置项
    )

    # -------- 本地开发开关 --------
    # True  → 使用本地配置
    # False → 使用.env线上配置
    LOCAL_DEV: bool = True
    

    # 基础项目配置
    PROJECT_NAME: str = "AI Decision Platform"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    # 持久化日志：True 时将日志写入项目根下 LOG_FILE_DIR，并按天分子目录轮转
    # 环境变量：LOG_FILE_ENABLED, LOG_FILE_DIR, LOG_FILE_MAX_BYTES
    LOG_FILE_ENABLED: bool = True
    LOG_FILE_DIR: str = "logs"
    LOG_FILE_MAX_BYTES: int = 1048576  # 1 MiB，单文件超限后同一天递增 02、03...
    # cost 异步写盘队列上限（有界）；满时丢弃新记录并触发 logging 的 handleError（不阻塞业务线程）
    LOG_COST_QUEUE_MAXSIZE: int = 8192

    # ------------------------------
    # MySQL
    # ------------------------------
    MYSQL_HOST: str
    MYSQL_PASSWORD: str
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_DB: str = "shopify_ai"
    MYSQL_POOL_SIZE: int = 10
    MYSQL_MAX_OVERFLOW: int = 20

    # ------------------------------
    # Redis
    # ------------------------------
    REDIS_HOST: str
    REDIS_PASSWORD: str
    REDIS_DB: int = 0
    REDIS_PORT: int = 6379

    # ------------------------------
    # Postgres (LangGraph)
    # ------------------------------
    POSTGRES_HOST: str
    POSTGRES_PASSWORD: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_DB: str = "lg_checkpoints"
    POSTGRES_POOL_MIN: int = 1
    POSTGRES_POOL_MAX: int = 10

    # ------------------------------
    # 阿里云 OSS
    # ------------------------------
    AK: str
    SK: str
    Endpoint: str = "oss-ap-southeast-1.aliyuncs.com"
    Bucket: str = "video-upload-shopai"
    OSS: str = "https://video-upload-shopai.oss-ap-southeast-1.aliyuncs.com"

    # ------------------------------
    # LangGraph  checkpoint 清理策略
    # ------------------------------
    CHECKPOINT_SWEEP_ENABLED: bool = True
    CHECKPOINT_TTL_DAYS: int = 14   #保留14天的历史记录
    CHECKPOINT_SWEEP_INTERVAL_HOURS: int = 24

    # ------------------------------
    # JWT 认证
    # ------------------------------
    JWT_SECRET_KEY: str
    JWT_REFRESH_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480    #6小时登录才过期
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7天

    # ------------------------------
    # Cookie 配置
    # ------------------------------
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: Optional[str] = None
    ACCESS_COOKIE_NAME: str = "access_token"
    REFRESH_COOKIE_NAME: str = "refresh_token"

    # ------------------------------
    # 热点缓存
    # ------------------------------
    #热点逻辑缓存2小时过期（直接用API抓取的热点）
    HOT_TRENDS_LOGICAL_TTL_SECONDS: int = 7200
    PRELOADING_HOT_TRENDS: bool = False
    #热点分析缓存7天过期（调用大模型对热点进行总结）
    HOT_TRENDS_ANALYSIS_CACHE_TTL_SECONDS: int = 7 * 24 * 3600
    HOT_TRENDS_ANALYSIS_VERSION: str = "v2"
    #热点与品牌匹配度计算7天过期（调用大模型对热点和品牌进行匹配度计算）
    HOT_TRENDS_MATCH_CACHE_TTL_SECONDS: int = 7 * 24 * 3600
    HOT_TRENDS_MATCH_VERSION: str = "v3"
    # TikTok hashtag 抓取并分析后的整批结果缓存，默认 6 小时
    TIKTOK_HASHTAG_TRENDS_CACHE_TTL_SECONDS: int = 6 * 3600
    TIKTOK_HASHTAG_TRENDS_CACHE_VERSION: str = "v1"

    # ------------------------------
    # 邮件
    # ------------------------------
    EMAIL_ENABLED: bool = True
    EMAIL_SMTP_HOST: str = "smtp.qq.com"
    EMAIL_SMTP_PORT: int = 465
    EMAIL_USERNAME: Optional[str] = "2768843481@qq.com"
    EMAIL_PASSWORD: Optional[str] = "fcgmkatobgojdeeh"
    EMAIL_FROM_NAME: str = "AI Decision"
    EMAIL_USE_SSL: bool = True

    # ------------------------------
    # WebSocket 心跳
    # ------------------------------
    # 每隔1分钟发一次心跳，如果超过一分半钟没有收到pong判定次数+1
    WS_HEARTBEAT_INTERVAL_SECONDS: int = 60
    WS_PONG_TIMEOUT_SECONDS: int = 150

    # ------------------------------
    # 跨域
    # ------------------------------
    ALLOWED_ORIGINS: str = "https://shop-ai.cc,https://www.shop-ai.cc,http://127.0.0.1:8000"

    # ------------------------------
    # Shopify
    # ------------------------------
    SHOPIFY_API_KEY: str
    SHOPIFY_API_SECRET: str
    SHOPIFY_API_VERSION: str = "2026-01"
    SHOPIFY_REDIRECT_URI: str

    # ------------------------------
    # 模型服务 API
    # ------------------------------
    SEEDANCE_VIDEO_API_KEY: str
    LLM_API_KEY: Optional[str] = None
    LLM_API_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL_V4_DEEPSEEK: str = "deepseek-v4-pro"
    LLM_MODEL_36_PLUS: str = "deepseek-r1"
    LLM_MODEL_35_PLUS: str = "qwen3.5-plus"
    LLM_MODEL_36_FLASH: str = "qwen3.6-flash-2026-04-16"
    # 热点批量分析 prompt 较大，默认放宽，避免网关/服务端慢导致断连（可通过环境变量覆盖）
    LLM_REQUEST_TIMEOUT_SECONDS: float = 420.0

    VOLCENGINE_API_KEY: str
    AGENT_MODEL_NAME: str = "doubao-seed-1-8-251228"
    VOLCENGINE_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"

    TAVILY_API_KEY: str
    SERPAPI_API_KEY: Optional[str] = None
    APIFY_API_KEY:str="apify_api_0fhtifAMQ2AflH8JR3DhKnfT5rEdaN0kEYqb"
    APIFY_TIKTOK_ACTOR_ID:str="GdWCkxBtKWOsKjdch"
    #竟品信息过期时间
    COMPETITOR_CACHE_TTL: int = 7200

    SEEDANCE2_API_KEY: str = "f3a44c8c-783c-492c-bf5d-1f6d3b671ac3"
    SEEDANCE2_MODEL_ID: str = "doubao-seedance-2-0-260128"
    SEEDANCE2_API_ENDPOINT: str = "https://ark.cn-beijing.volces.com/api/v3"

    # ------------------------------
    # 本地开发专用覆盖配置
    # ------------------------------
    _LOCAL_MYSQL_HOST: str = "localhost"
    _LOCAL_MYSQL_PASSWORD: str = "123456"
    _LOCAL_MYSQL_USER: str = "root"
    _LOCAL_MYSQL_PORT: int = 3306
    _LOCAL_REDIS_HOST: str = "127.0.0.1"
    _LOCAL_REDIS_PASSWORD: str = "123456"
    _LOCAL_SHOPIFY_REDIRECT_URL: str = "http://127.0.0.1:8000/api/v1/auth/shopify/callback"

    # ==============================
    # 校验器（必须写在类内部！）
    # ==============================
    @model_validator(mode="after")
    def _apply_local_dev_overrides(self) -> "Settings":
        """LOCAL_DEV=True 时强制使用本地配置，优先级最高"""
        if self.LOCAL_DEV:
            self.MYSQL_HOST = self._LOCAL_MYSQL_HOST
            self.MYSQL_PASSWORD = self._LOCAL_MYSQL_PASSWORD
            self.MYSQL_USER = self._LOCAL_MYSQL_USER
            self.MYSQL_PORT = self._LOCAL_MYSQL_PORT
            self.REDIS_HOST = self._LOCAL_REDIS_HOST
            self.REDIS_PASSWORD = self._LOCAL_REDIS_PASSWORD
            self.SHOPIFY_REDIRECT_URI = self._LOCAL_SHOPIFY_REDIRECT_URL
            self.COOKIE_SECURE = False  # 本地开发关闭 Secure Cookie
        return self

    @model_validator(mode="after")
    def _validate_ws_heartbeat(self) -> "Settings":
        """WebSocket 心跳安全校验"""
        if self.WS_PONG_TIMEOUT_SECONDS <= self.WS_HEARTBEAT_INTERVAL_SECONDS:
            raise ValueError(
                f"WS_PONG_TIMEOUT({self.WS_PONG_TIMEOUT_SECONDS}) 必须大于 WS_HEARTBEAT_INTERVAL({self.WS_HEARTBEAT_INTERVAL_SECONDS})"
            )
        return self


# ==============================
# 单例获取配置（全局唯一）
# ==============================
@lru_cache(maxsize=None)
def get_settings() -> Settings:
    try:
        return Settings()
    except (PermissionError, OSError):
        # 无权限读取 .env 时降级使用环境变量 + 默认值
        return Settings(_env_file=None)

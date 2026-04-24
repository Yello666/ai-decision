from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # 忽略 .env 中多余的配置项，防止报错
    )

    # -------- 本地开发开关 --------
    # True  → 使用下方 _LOCAL_* 的虚拟机地址（忽略 .env 中的数据库/Redis 配置）
    # False → 使用 .env 中的云数据库配置（部署时设为 False）
    LOCAL_DEV: bool = False


    PROJECT_NAME: str = "AI Decision Platform"
    API_V1_PREFIX: str = "/api/v1"

    # Database（默认值会被 .env 覆盖，再被 LOCAL_DEV 覆盖）
    MYSQL_HOST: str 
    MYSQL_PASSWORD: str 
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_DB: str = "shopify_ai"

    MYSQL_POOL_SIZE: int = 10
    MYSQL_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_HOST: str
    REDIS_PASSWORD: str
    REDIS_DB: int = 0
    REDIS_PORT: int = 6379

    # Postgres（LangGraph checkpointer 专用）
    POSTGRES_HOST: str
    POSTGRES_PASSWORD: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_DB: str = "lg_checkpoints"
    # 连接池
    POSTGRES_POOL_MIN: int = 1
    POSTGRES_POOL_MAX: int = 10

    # -------- LangGraph checkpoint 清理策略 --------
    # 工作流到达终态（approve/cancel/respond→END）会主动删除；
    # 下列参数用于被动清理（兜底废弃/崩溃会话）。
    CHECKPOINT_SWEEP_ENABLED: bool = True
    # 最近活跃时间早于此值的 thread 会被清理
    CHECKPOINT_TTL_DAYS: int = 14
    # 后台扫描周期（小时）
    CHECKPOINT_SWEEP_INTERVAL_HOURS: int = 24


    # -------- 本地开发配置（仅 LOCAL_DEV=True 时生效） --------
    _LOCAL_MYSQL_HOST: str = "192.168.64.2"
    _LOCAL_MYSQL_PASSWORD: str = "123456"
    _LOCAL_MYSQL_USER: str = "root"
    _LOCAL_MYSQL_PORT: int = 3306
    _LOCAL_REDIS_HOST: str = "192.168.64.2"
    _LOCAL_REDIS_PASSWORD: str = "123456"
    _LOCAL_SHOPIFY_REDIRECT_URL:str = "http://127.0.0.1:8000/api/v1/auth/shopify/callback"

    @model_validator(mode="after")
    def _apply_local_dev_overrides(self) -> "Settings":
        """LOCAL_DEV=True 时，强制使用虚拟机地址，优先级高于 .env"""
        if self.LOCAL_DEV:
            self.MYSQL_HOST = self._LOCAL_MYSQL_HOST
            self.MYSQL_PASSWORD = self._LOCAL_MYSQL_PASSWORD
            self.MYSQL_USER = self._LOCAL_MYSQL_USER
            self.MYSQL_PORT = self._LOCAL_MYSQL_PORT
            self.REDIS_HOST = self._LOCAL_REDIS_HOST
            self.REDIS_PASSWORD = self._LOCAL_REDIS_PASSWORD
            self.SHOPIFY_REDIRECT_URI=self._LOCAL_SHOPIFY_REDIRECT_URL
            # 本地 HTTP 开发：Secure cookie 不会在 http 下发送
            self.COOKIE_SECURE = False
        return self

    @model_validator(mode="after")
    def _validate_ws_heartbeat_config(self) -> "Settings":
        """WebSocket：pong 超时时长必须大于心跳间隔，否则必然误判离线。"""
        if self.WS_PONG_TIMEOUT_SECONDS <= self.WS_HEARTBEAT_INTERVAL_SECONDS:
            raise ValueError(
                "WS_PONG_TIMEOUT_SECONDS must be greater than WS_HEARTBEAT_INTERVAL_SECONDS "
                f"(got pong_timeout={self.WS_PONG_TIMEOUT_SECONDS}, "
                f"heartbeat_interval={self.WS_HEARTBEAT_INTERVAL_SECONDS})"
            )
        return self


    # JWT
    JWT_SECRET_KEY: str
    JWT_REFRESH_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    # 登录30分钟后会过期
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 60 * 24 * 7

    # -------- Cookie 会话配置 --------
    # 用于 HttpOnly cookie 鉴权；跨站点前后端部署时需要 SAMESITE="none" + SECURE=True。
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "lax"  # lax | strict | none
    COOKIE_DOMAIN: Optional[str] = None
    ACCESS_COOKIE_NAME: str = "access_token"
    REFRESH_COOKIE_NAME: str = "refresh_token"

    # -------- 热点缓存配置 --------
    HOT_TRENDS_LOGICAL_TTL_SECONDS: int = 600 #10分钟更新1次
    # 是否启动系统时预加载热点
    PRELOADING_HOT_TRENDS: bool = False
    # 单热点 LLM 分析结果缓存：命中则直接复用，不再调用大模型
    HOT_TRENDS_ANALYSIS_CACHE_TTL_SECONDS: int = 7 * 24 * 3600  # 7 天
    # 模型/Prompt 升级时修改此版本号即可让历史分析结果失效
    HOT_TRENDS_ANALYSIS_VERSION: str = "v1"

    # (品牌, 热点) 匹配度缓存：同一品牌-热点组合只计算一次
    HOT_TRENDS_MATCH_CACHE_TTL_SECONDS: int = 7 * 24 * 3600  # 7 天
    HOT_TRENDS_MATCH_VERSION: str = "v1"

    LOG_LEVEL: str = "INFO"

    # -------- WebSocket 心跳/超时配置 --------
    # 服务端发送 {"event":"ping"} 的间隔（秒）
    WS_HEARTBEAT_INTERVAL_SECONDS: int = 30
    # 超过该时长未收到任何合法客户端 JSON 消息即判定离线并断开连接（秒）
    # HEARTBEAT_INTERVAL * 2 + 余量，避免偶发丢包误判
    WS_PONG_TIMEOUT_SECONDS: int = 75

    # 允许的前端域名：在有cookie和Authorization请求头的时候需要具体域名，在这里配置即可。其余时候不会使用这里的域名
    #多个域名使用,分隔
    ALLOWED_ORIGINS: str = "https://shop-ai.cc,https://www.shop-ai.cc,http://127.0.0.1:8000"

    # ShopifyhopifyAPP的client id,自己携带这个就可以被shopifyAPP认为是client，发起请求
    SHOPIFY_API_KEY: str
    SHOPIFY_API_SECRET: str
    SHOPIFY_API_VERSION: str = "2026-01"
    SHOPIFY_REDIRECT_URI: str

    AUTODL_SERVICE_URL: str = "http://localhost:8001/predict"


    # Seedance 1.5 Pro 视频生成 (火山引擎方舟)
    SEEDANCE_VIDEO_API_KEY: str

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

    # Competitor cache（2h）
    COMPETITOR_CACHE_TTL: int = 7200


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

# 必须在导入其他会打日志的业务模块之前配置 root logging（含 Docker 下 uvicorn 子进程）
from app.core.logger import configure_logging, shutdown_cost_queue_logging

configure_logging()

from contextlib import asynccontextmanager  # 新增：必须导入
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import router as api_v1_router
from app.core.config import get_settings
from app.core.exceptions import (
    AppException,
    app_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.hot_trends_cache import preload_hot_trends_cache
from app.core.responses import success
from app.db.mysql import engine, db_url
from app.db.postgres import (
    close_postgres_checkpointer,
    init_postgres_checkpointer,
    start_checkpoint_cleanup_task,
)
from app.db.redis import get_redis_client, close_redis
from app.models import Base
from app.services.hotspot_service.collect_hostspot import collect_and_format_hot_data_async
from app.services.hotspot_service.recommend_email_scheduler import create_recommend_email_scheduler

settings = get_settings()
logger = logging.getLogger(__name__)


# 生命周期函数（启动+关闭）
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 兜底：uvicorn/worker 若在 load 之后又改过 logging，这里再挂载一次北京时间与 Handler
    configure_logging()

    # 启动：创建数据库表 + 测试 Redis 连接
    logger.info("Database URL: %s", db_url)
    Base.metadata.create_all(bind=engine)
    redis_client = get_redis_client()
    # Mock redis might not support ping or wait, handle gracefully
    try:
        await redis_client.ping()  # 异步 ping（适配异步客户端）
    except Exception as e:
        print(f"Redis ping failed (might be mock): {e}")

    # LangGraph checkpointer：初始化 Postgres 连接池并确保表结构存在
    try:
        await init_postgres_checkpointer()
    except Exception:
        logger.exception("Postgres checkpointer 初始化失败")
        raise

    # 启动后台清理任务：兜底清理长期废弃/崩溃的 thread
    start_checkpoint_cleanup_task()

    if settings.PRELOADING_HOT_TRENDS:
        try:
            await preload_hot_trends_cache(loader=collect_and_format_hot_data_async)
            logger.info("热点预加载完成")
        except Exception:
            logger.exception("热点预加载失败")

    scheduler = create_recommend_email_scheduler()
    if scheduler is not None:
        scheduler.start()
        logger.info("热点推荐邮件定时器已启动")

    yield  # 应用运行

    await close_postgres_checkpointer()
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("热点推荐邮件定时器已关闭")
    await close_redis()
    shutdown_cost_queue_logging()


# 初始化 FastAPI
app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan
)

# CORS 中间件
# 注意：当 allow_origins 含 "*" 时，不能使用 allow_credentials=True（CORS 规范不允许），否则浏览器收不到有效 Access-Control-Allow-Origin
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
_credentials = "*" not in _origins
if _credentials and not _origins:
    _origins = ["*"]
    _credentials = False
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 异常处理器
@app.exception_handler(AppException)
def handle_app_exception(request, exc: AppException):
    return app_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request, exc: StarletteHTTPException):
    return http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
def handle_validation_exception(request, exc: RequestValidationError):
    return validation_exception_handler(request, exc)


# 健康检查
@app.get("/health")
def health_check():
    return success({"status": "ok"})


# 路由注册
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

# 静态文件
# app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

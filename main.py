from contextlib import asynccontextmanager  # 新增：必须导入
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
from app.core.logger import configure_logging
from app.core.responses import success
import logging
from app.db.mysql import engine, db_url
from app.db.redis import get_redis_client  # 确保返回异步客户端
from app.models import Base

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


# 生命周期函数（启动+关闭）
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：创建数据库表 + 测试 Redis 连接
    logger.info("Database URL: %s", db_url)
    Base.metadata.create_all(bind=engine)
    redis_client = get_redis_client()
    # Mock redis might not support ping or wait, handle gracefully
    try:
        await redis_client.ping()  # 异步 ping（适配异步客户端）
    except Exception as e:
        print(f"Redis ping failed (might be mock): {e}")

    yield  # 应用运行

    # 关闭：安全释放资源
    await redis_client.close()  # 异步关闭（适配异步客户端）
    try:
        await redis_client.connection_pool.disconnect()  # 彻底断开连接池（可选，更安全）
    except:
        pass


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
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

"""LangGraph 的 Postgres checkpointer 连接池、单例与清理策略。

职责：
- 维护进程级 ``AsyncConnectionPool``（psycopg v3）。
- 暴露 ``AsyncPostgresSaver`` 单例给各处 Graph compile 使用。
- 提供主动清理（``delete_thread``）与被动清理（``sweep_stale_threads`` + 后台循环）。
- 由 FastAPI lifespan 负责初始化 / 关闭。

注意：
- ``AsyncPostgresSaver`` 要求连接 ``autocommit=True`` 且 ``row_factory=dict_row``。
- ``setup()`` 幂等，首次运行会自动创建 ``checkpoints`` 等 4 张表。
- 被动清理依赖 LangGraph 的 ``checkpoint_id`` 为 UUIDv6（时间有序），从中解析时间戳。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: Optional[AsyncConnectionPool] = None
_checkpointer: Optional[AsyncPostgresSaver] = None
_sweep_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# DSN / 连接池
# ---------------------------------------------------------------------------


def get_postgres_dsn() -> str:
    """拼装 Postgres 连接串，按需追加 SSL 参数。"""
    s = get_settings()
    pwd = s.POSTGRES_PASSWORD.replace("@", "%40") if s.POSTGRES_PASSWORD else ""
    dsn = (
        f"postgresql://{s.POSTGRES_USER}:{pwd}@"
        f"{s.POSTGRES_HOST}:{s.POSTGRES_PORT}/{s.POSTGRES_DB}"
    )
    return dsn


def _mask_dsn(dsn: str) -> str:
    """日志脱敏，避免把密码打出来。"""
    return re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1***\3", dsn)


async def init_postgres_checkpointer() -> AsyncPostgresSaver:
    """启动阶段调用：建连接池、建表、返回共享 checkpointer。"""
    global _pool, _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    s = get_settings()
    dsn = get_postgres_dsn()
    logger.info("Postgres checkpointer DSN: %s", _mask_dsn(dsn))

    # check=check_connection：交出连接前做 SELECT 1 心跳，stale/broken 连接会被
    # 自动丢弃并重建，避免把 [BAD] 连接交给 LangGraph checkpointer 造成
    # `consuming input failed: server closed the connection unexpectedly` 连锁错误。
    # max_idle：主动回收长时间空闲的连接，减少云 PG / 中间件静默踢掉 TCP 造成的 stale。
    # TCP keepalive：让内核尽早发现死掉的 TCP 连接，与应用层 check 互补。
    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=s.POSTGRES_POOL_MIN,
        max_size=s.POSTGRES_POOL_MAX,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
        max_idle=300.0,
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    _pool = pool
    _checkpointer = checkpointer
    logger.info(
        "Postgres checkpointer ready (pool min=%d, max=%d)",
        s.POSTGRES_POOL_MIN,
        s.POSTGRES_POOL_MAX,
    )
    return checkpointer


def get_checkpointer() -> AsyncPostgresSaver:
    """业务代码获取共享 checkpointer。必须在 lifespan 初始化之后调用。"""
    if _checkpointer is None:
        raise RuntimeError(
            "Postgres checkpointer 尚未初始化，"
            "请确认 FastAPI lifespan 中已调用 init_postgres_checkpointer()。"
        )
    return _checkpointer


async def close_postgres_checkpointer() -> None:
    """应用关闭时释放连接池（同时停止后台清理任务）。"""
    global _pool, _checkpointer
    await stop_checkpoint_cleanup_task()
    if _pool is not None:
        await _pool.close()
        _pool = None
    _checkpointer = None
    logger.info("Postgres checkpointer closed")


# ---------------------------------------------------------------------------
# 主动清理：工作流到终态后立即删除该 thread 的所有 checkpoint
# ---------------------------------------------------------------------------


async def delete_thread(thread_id: str) -> None:
    """删除指定 thread 的全部 checkpoint 与 pending writes。

    调用点：
    - pricing 工作流到达 apply/cancel → END 后；
    - video 工作流到达 respond → END 后。

    失败仅记日志不抛异常，避免清理失败影响主流程。
    """
    if _checkpointer is None:
        logger.warning("delete_thread called before checkpointer init: %s", thread_id)
        return
    try:
        await _checkpointer.adelete_thread(thread_id)
        logger.info("Checkpoint cleaned: thread_id=%s", thread_id)
    except Exception:
        logger.exception("Failed to delete thread checkpoints: %s", thread_id)


# ---------------------------------------------------------------------------
# 被动清理：定时扫描陈旧 thread（LangGraph checkpoint_id 为 UUIDv6，时间有序）
# ---------------------------------------------------------------------------


# Gregorian 1582-10-15 到 Unix 1970-01-01 的偏移，单位为 100 纳秒。
_GREGORIAN_TO_UNIX_100NS = 0x01B21DD213814000


def _uuid6_time(uuid_str: str) -> Optional[datetime]:
    """从 UUIDv6 字符串中提取时间戳。

    UUIDv6 布局（MSB→LSB）：48 位 time_high | 4 位 version | 12 位 time_low | 62 位 rest。
    60 位 time 表示自 1582-10-15 起的 100ns 间隔数。
    """
    try:
        u = uuid.UUID(uuid_str)
    except (ValueError, AttributeError):
        return None
    if u.version != 6:
        return None
    i = u.int
    time_high = (i >> 80) & 0xFFFFFFFFFFFF
    time_low = (i >> 64) & 0xFFF
    ts_100ns = (time_high << 12) | time_low
    unix_ns = (ts_100ns - _GREGORIAN_TO_UNIX_100NS) * 100
    if unix_ns <= 0:
        return None
    try:
        return datetime.fromtimestamp(unix_ns / 1e9, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


async def sweep_stale_threads(max_age_days: int) -> int:
    """删除最近活跃时间早于 ``max_age_days`` 的 thread。

    通过 ``MAX(checkpoint_id)`` 取每个 thread 最新 checkpoint_id（UUIDv6 字典序=时间序），
    解析其时间戳判断是否过期。无法解析时间戳的 thread 会被跳过（保守策略）。

    返回：被删除的 thread 数量。
    """
    if _pool is None or _checkpointer is None:
        logger.warning("sweep_stale_threads called before init")
        return 0
    if max_age_days <= 0:
        return 0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    stale_threads: list[str] = []

    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, MAX(checkpoint_id) AS latest_id "
                "FROM checkpoints GROUP BY thread_id"
            )
            rows = await cur.fetchall()

    for row in rows:
        thread_id = row["thread_id"]
        latest_id = row["latest_id"]
        ts = _uuid6_time(latest_id) if latest_id else None
        if ts is None or ts >= cutoff:
            continue
        stale_threads.append(thread_id)

    deleted = 0
    for tid in stale_threads:
        try:
            await _checkpointer.adelete_thread(tid)
            deleted += 1
        except Exception:
            logger.exception("Failed to sweep stale thread: %s", tid)

    if deleted:
        logger.info(
            "Checkpoint sweeper: deleted %d stale threads (older than %d days)",
            deleted,
            max_age_days,
        )
    else:
        logger.debug(
            "Checkpoint sweeper: no stale threads found (scanned=%d)", len(rows)
        )
    return deleted


# ---------------------------------------------------------------------------
# 后台定时清理任务
# ---------------------------------------------------------------------------


async def _sweep_loop(interval_hours: int, ttl_days: int) -> None:
    """后台循环：每 interval_hours 触发一次 sweep。"""
    interval = max(interval_hours, 1) * 3600
    logger.info(
        "Checkpoint sweeper started (interval=%dh, ttl=%dd)",
        interval_hours,
        ttl_days,
    )
    while True:
        try:
            await asyncio.sleep(interval)
            await sweep_stale_threads(ttl_days)
        except asyncio.CancelledError:
            logger.info("Checkpoint sweeper cancelled")
            raise
        except Exception:
            logger.exception("Checkpoint sweeper iteration failed; will retry next cycle")


def start_checkpoint_cleanup_task() -> None:
    """启动后台清理任务（按 settings 配置）；幂等。"""
    global _sweep_task
    if _sweep_task is not None and not _sweep_task.done():
        return
    s = get_settings()
    if not s.CHECKPOINT_SWEEP_ENABLED:
        logger.info("Checkpoint sweeper disabled by settings")
        return
    _sweep_task = asyncio.create_task(
        _sweep_loop(s.CHECKPOINT_SWEEP_INTERVAL_HOURS, s.CHECKPOINT_TTL_DAYS),
        name="checkpoint-sweeper",
    )


async def stop_checkpoint_cleanup_task() -> None:
    """停止后台清理任务。"""
    global _sweep_task
    if _sweep_task is None:
        return
    _sweep_task.cancel()
    try:
        await _sweep_task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Checkpoint sweeper exit with error")
    _sweep_task = None

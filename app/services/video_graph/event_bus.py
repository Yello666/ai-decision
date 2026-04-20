"""
Per-thread 事件总线 —— SSE 推送管道。

设计要点:
  - Key 为 thread_id，Value 为 1..N 个 asyncio.Queue（一个订阅者一个 Queue，支持多端同时订阅）。
  - 后端节点通过 publish(thread_id, event, data) 把进度事件喂给总线。
  - SSE 路由通过 subscribe() 拿到自己的 Queue，for-ever 消费。
  - 总线只做"实时感"，不负责持久化；断线重连由前端先拉 /state 兜底。

为什么不直接用 LangGraph 原生 astream？
  1. 我们需要自定义事件类型（segment_done / human_action_required），astream 难以表达；
  2. Graph 在 interrupt 后会退出 async 调用栈，但 SSE 订阅要跨 /create 与 /resume 存活，
     需要一个独立于 Graph 生命周期的中间管道。

注意:
  - 这是进程内实现，适合单副本部署。多副本需替换为 Redis Pub/Sub。
  - publish 使用 put_nowait；Queue 满时直接丢弃最旧事件，保证发布端永远非阻塞。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# SSE 事件类型白名单（与前端契约保持一致）
EventType = str  # 常用: "progress" | "segment_done" | "human_action_required" | "done" | "error" | "state" | "ping"

# 单个订阅者的缓冲大小；溢出时丢弃最旧事件
_QUEUE_MAXSIZE = 256
# 终态事件类型：发布后总线会主动"封顶"通知所有订阅者
_TERMINAL_EVENTS = {"done", "error"}


@dataclass
class BusEvent:
    """总线事件结构，序列化给 SSE 后即 `event: <type>\\ndata: <json>\\n\\n`"""

    event: EventType
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())


class EventBus:
    """多订阅者、进程内、内存态事件总线。"""

    def __init__(self) -> None:
        # thread_id -> list[Queue]
        self._subscribers: dict[str, list[asyncio.Queue[BusEvent]]] = {}
        # thread_id -> 最后一次事件（给后加入的订阅者做"最近态补发"）
        self._last_event: dict[str, BusEvent] = {}
        # thread_id -> 是否已终态（done / error 后不再接受新订阅的长连接）
        self._terminated: set[str] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 订阅 / 取消订阅
    # ------------------------------------------------------------------
    async def subscribe(self, thread_id: str) -> asyncio.Queue[BusEvent]:
        """订阅指定 thread 的事件流。返回一个全新的 Queue。"""
        queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.setdefault(thread_id, []).append(queue)
            last = self._last_event.get(thread_id)
            terminated = thread_id in self._terminated

        # 新订阅者立刻收到最近一次事件，避免"空屏"
        if last is not None:
            try:
                queue.put_nowait(last)
            except asyncio.QueueFull:
                pass

        # 如果任务已经终态，立即补发一个 done/error 让新订阅者快速感知
        if terminated and last is not None and last.event in _TERMINAL_EVENTS:
            # last 里其实就是终态事件；这里什么都不用额外做
            pass
        return queue

    async def unsubscribe(self, thread_id: str, queue: asyncio.Queue[BusEvent]) -> None:
        async with self._lock:
            subs = self._subscribers.get(thread_id)
            if not subs:
                return
            try:
                subs.remove(queue)
            except ValueError:
                return
            if not subs:
                self._subscribers.pop(thread_id, None)

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    async def publish(
        self,
        thread_id: Optional[str],
        event: EventType,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        向所有订阅者投递事件。
        发布端永远不阻塞；Queue 满时丢弃最旧事件，保证生产者吞吐。
        """
        if not thread_id:
            return

        bus_event = BusEvent(event=event, data=data or {})

        async with self._lock:
            self._last_event[thread_id] = bus_event
            if event in _TERMINAL_EVENTS:
                self._terminated.add(thread_id)
            subs = list(self._subscribers.get(thread_id, []))

        for q in subs:
            _safe_put(q, bus_event)

    # ------------------------------------------------------------------
    # 垃圾回收 —— 任务彻底结束一段时间后清理内存
    # ------------------------------------------------------------------
    async def cleanup(self, thread_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(thread_id, None)
            self._last_event.pop(thread_id, None)
            self._terminated.discard(thread_id)


def _safe_put(queue: asyncio.Queue[BusEvent], item: BusEvent) -> None:
    """非阻塞投递；Queue 满时丢弃队首最旧事件后重试。"""
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        pass
    try:
        _ = queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning("EventBus queue still full after drop, event dropped: %s", item.event)


# ──────────────────────────────────────────────
# 全局单例
# ──────────────────────────────────────────────
_bus: EventBus = EventBus()


def get_event_bus() -> EventBus:
    return _bus


async def publish_event(
    thread_id: Optional[str],
    event: EventType,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """便捷包装：节点内调用 `await publish_event(state['thread_id'], 'progress', {...})`。"""
    await _bus.publish(thread_id, event, data)


__all__ = [
    "BusEvent",
    "EventBus",
    "get_event_bus",
    "publish_event",
]

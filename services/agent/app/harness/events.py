"""事件推送系统 — 内存 Pub/Sub

支持发布 Agent 执行过程中的实时事件，供 SSE 等消费端订阅。
移植自 QuantHatch agent_engine，删除 RedisEventPublisher（保留内存版本）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Protocol

logger = logging.getLogger("agent_engine.events")


class EventPublisher(Protocol):
    """事件发布者协议 — 允许自定义实现（WebSocket / 内存等）"""

    async def publish(self, channel: str, event: dict) -> None:
        """发布事件到指定频道"""
        ...


class InMemoryEventPublisher:
    """内存事件发布器 — 用于测试或单进程场景"""

    def __init__(self) -> None:
        self.events: dict[str, list[dict]] = {}  # channel -> events

    async def publish(self, channel: str, event: dict) -> None:
        event["ts"] = time.time()
        self.events.setdefault(channel, []).append(event)

    def get_events(self, channel: str) -> list[dict]:
        return self.events.get(channel, [])

    def clear(self) -> None:
        self.events.clear()


class NullEventPublisher:
    """空事件发布器 — 不发布任何事件"""

    async def publish(self, channel: str, event: dict) -> None:
        pass


# ---- 订阅式事件发布器 ----

class SubscribableEventPublisher:
    """实时订阅式事件发布器：每订阅者一个 asyncio.Queue，publish 扇出到所有订阅者，
    并存入 per-channel ring buffer（重连补发优化；权威历史在 DB agent_event 表）。
    seq 不在此分配——event 里若有 seq 由调用方填好。

    注意：asyncio.Queue 绑定到创建它的事件循环（Python ≥ 3.10 已无显式绑定，
    但 subscribe/publish 必须在同一 event loop 线程内调用，不跨线程共享实例。）
    """

    def __init__(self, ring_size: int = 512) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._ring: dict[str, deque] = {}
        self._ring_size = ring_size

    def subscribe(self, channel: str) -> asyncio.Queue:
        """订阅 channel，返回专属 Queue；调用方从 Queue 消费事件。"""
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(channel, []).append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        """取消订阅；不存在则忽略。列表为空时清理 key 以避免内存泄漏。"""
        subs = self._subs.get(channel)
        if subs is None:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            del self._subs[channel]

    async def publish(self, channel: str, event: dict) -> None:
        """1) 存入 ring buffer  2) 扇出到所有订阅者的 Queue。"""
        # ring buffer
        if channel not in self._ring:
            self._ring[channel] = deque(maxlen=self._ring_size)
        self._ring[channel].append(event)

        # 扇出
        for q in list(self._subs.get(channel, [])):
            q.put_nowait(event)

    def ring(self, channel: str, after_seq: int = 0) -> list[dict]:
        """返回 ring 中 seq > after_seq 的事件（无 seq 字段视为 0）。"""
        return [
            e for e in self._ring.get(channel, [])
            if e.get("seq", 0) > after_seq
        ]


# ---- 事件类型常量 ----

class EventType:
    RUN_START = "run_start"
    LLM_START = "llm_start"
    TOOLS_START = "tools_start"
    ROUND_COMPLETE = "round_complete"
    RUN_COMPLETE = "run_complete"
    MEMORY_CREATED = "memory_created"
    ERROR = "error"
    # P1-4 新增
    TOOL_CONFIRM_REQUIRED = "tool_confirm_required"
    TOKEN = "token"
    CITATIONS = "citations"
    PAUSED = "paused"
    RESUMED = "resumed"
    CANCELLED = "cancelled"


# ---- 便捷函数 ----

async def publish_run_event(
    publisher: EventPublisher,
    run_id: str,
    event: dict,
) -> None:
    """发布 Run 执行事件"""
    try:
        await publisher.publish(f"run:{run_id}:events", event)
    except Exception as e:
        logger.warning(f"[Events] Publish run event failed: {e}")

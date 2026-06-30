"""双层记忆系统 — 短期摘要 + 长期认知

提供通用的记忆管理框架，不依赖特定 ORM。
通过 MemoryStore 协议实现存储层解耦。
移植自 QuantHatch agent_engine，仅保留 InMemoryStore（无 Redis 依赖）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger("agent_engine.memory")


# ---- 记忆类型 ----

class MemoryType:
    RUN_SUMMARY = "run_summary"   # 单次 Run 摘要
    REFLECTION = "reflection"     # 复盘（对比预期 vs 实际）
    COGNITION = "cognition"       # 长期认知（从多次执行中提炼）


# ---- 记忆数据结构 ----

@dataclass
class Memory:
    """单条记忆"""
    id: str = ""
    agent_id: str = ""
    memory_type: str = MemoryType.RUN_SUMMARY
    content: str = ""
    metadata: dict = field(default_factory=dict)
    run_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())


# ---- 存储协议 ----

class MemoryStore(Protocol):
    """记忆存储协议 — 实现此接口对接不同后端（DB / 文件等）"""

    async def save(self, memory: Memory) -> None:
        """保存记忆"""
        ...

    async def query(
        self,
        agent_id: str,
        memory_types: list[str] | None = None,
        limit: int = 5,
        exclude_expired: bool = True,
    ) -> list[Memory]:
        """查询记忆，按优先级排序

        优先级: cognition > reflection > run_summary > 时间倒序
        """
        ...

    async def count_since(
        self,
        agent_id: str,
        since: datetime,
        memory_type: str | None = None,
    ) -> int:
        """统计指定时间后的记忆数量"""
        ...


# ---- 内存存储实现 ----

class InMemoryStore:
    """基于内存的记忆存储 — 用于测试或轻量场景"""

    def __init__(self) -> None:
        self._store: dict[str, list[Memory]] = {}  # agent_id -> memories

    async def save(self, memory: Memory) -> None:
        self._store.setdefault(memory.agent_id, []).append(memory)

    async def query(
        self,
        agent_id: str,
        memory_types: list[str] | None = None,
        limit: int = 5,
        exclude_expired: bool = True,
    ) -> list[Memory]:
        memories = self._store.get(agent_id, [])
        now = datetime.utcnow()

        # 过滤过期
        if exclude_expired:
            memories = [
                m for m in memories
                if m.expires_at is None or m.expires_at > now
            ]

        # 过滤类型
        if memory_types:
            memories = [m for m in memories if m.memory_type in memory_types]

        # 排序：cognition > reflection > run_summary > 时间倒序
        type_priority = {
            MemoryType.COGNITION: 0,
            MemoryType.REFLECTION: 1,
            MemoryType.RUN_SUMMARY: 2,
        }
        memories.sort(
            key=lambda m: (type_priority.get(m.memory_type, 9), -m.created_at.timestamp())
        )

        return memories[:limit]

    async def count_since(
        self,
        agent_id: str,
        since: datetime,
        memory_type: str | None = None,
    ) -> int:
        memories = self._store.get(agent_id, [])
        count = 0
        for m in memories:
            if m.created_at > since:
                if memory_type is None or m.memory_type == memory_type:
                    count += 1
        return count


# ---- 记忆服务 ----

class MemoryService:
    """记忆管理服务 — 封装记忆的创建、检索和提炼逻辑

    Args:
        store: 记忆存储后端
        short_term_ttl_days: 短期记忆过期天数
        max_context: 注入 prompt 的最大记忆条数
        cognition_interval: 每 N 次成功 Run 触发认知提炼
    """

    def __init__(
        self,
        store: MemoryStore,
        short_term_ttl_days: int = 30,
        max_context: int = 5,
        cognition_interval: int = 10,
    ) -> None:
        self.store = store
        self.short_term_ttl_days = short_term_ttl_days
        self.max_context = max_context
        self.cognition_interval = cognition_interval

    def _short_term_expiry(self) -> datetime:
        return datetime.utcnow() + timedelta(days=self.short_term_ttl_days)

    async def create_run_summary(
        self,
        agent_id: str,
        run_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> Memory:
        """创建 Run 摘要记忆"""
        memory = Memory(
            agent_id=agent_id,
            memory_type=MemoryType.RUN_SUMMARY,
            content=content,
            metadata=metadata or {},
            run_id=run_id,
            expires_at=self._short_term_expiry(),
        )
        await self.store.save(memory)
        logger.info(f"[Memory] Created run_summary: agent={agent_id}")
        return memory

    async def create_reflection(
        self,
        agent_id: str,
        content: str,
        metadata: dict | None = None,
        run_id: str | None = None,
    ) -> Memory:
        """创建复盘记忆"""
        memory = Memory(
            agent_id=agent_id,
            memory_type=MemoryType.REFLECTION,
            content=content,
            metadata=metadata or {},
            run_id=run_id,
            expires_at=self._short_term_expiry(),
        )
        await self.store.save(memory)
        logger.info(f"[Memory] Created reflection: agent={agent_id}")
        return memory

    async def get_context(
        self,
        agent_id: str,
        limit: int | None = None,
    ) -> str:
        """获取注入 Prompt 的记忆上下文文本

        按优先级排序: cognition > reflection > run_summary
        """
        memories = await self.store.query(
            agent_id=agent_id,
            limit=limit or self.max_context,
        )

        if not memories:
            return ""

        type_labels = {
            MemoryType.COGNITION: "Long-term Cognition",
            MemoryType.REFLECTION: "Reflection",
            MemoryType.RUN_SUMMARY: "Run Summary",
        }

        parts = []
        for m in memories:
            label = type_labels.get(m.memory_type, m.memory_type)
            parts.append(f"### {label}\n{m.content}")

        return "\n\n".join(parts)

    async def maybe_distill_cognition(
        self,
        agent_id: str,
        distill_fn: Any | None = None,
    ) -> Memory | None:
        """尝试从短期记忆提炼长期认知

        Args:
            agent_id: Agent ID
            distill_fn: 可选的自定义提炼函数
                signature: async (reflections: list[Memory]) -> str
                如果不提供，使用默认的统计规则提炼

        Returns:
            新创建的 cognition Memory，或 None（未到触发阈值）
        """
        # 查找上次 cognition 的时间
        cognitions = await self.store.query(
            agent_id=agent_id,
            memory_types=[MemoryType.COGNITION],
            limit=1,
        )
        since = cognitions[0].created_at if cognitions else datetime(2000, 1, 1)

        # 统计 since 之后的记忆数（用作 run 成功次数的近似）
        run_count = await self.store.count_since(
            agent_id=agent_id,
            since=since,
            memory_type=MemoryType.RUN_SUMMARY,
        )

        if run_count < self.cognition_interval:
            return None

        # 收集最近的 reflection
        reflections = await self.store.query(
            agent_id=agent_id,
            memory_types=[MemoryType.REFLECTION],
            limit=self.cognition_interval,
        )
        # 只取 since 之后的
        reflections = [r for r in reflections if r.created_at > since]

        if not reflections:
            return None

        # 提炼
        if distill_fn:
            content = await distill_fn(reflections)
        else:
            content = self._default_distill(reflections, run_count)

        memory = Memory(
            agent_id=agent_id,
            memory_type=MemoryType.COGNITION,
            content=content,
            metadata={
                "run_count": run_count,
                "reflection_count": len(reflections),
            },
            # cognition 不设过期时间（永久保留）
        )
        await self.store.save(memory)
        logger.info(
            f"[Memory] Distilled cognition: agent={agent_id}, runs={run_count}"
        )
        return memory

    @staticmethod
    def _default_distill(reflections: list[Memory], run_count: int) -> str:
        """默认提炼规则：统计汇总"""
        now = datetime.utcnow().strftime("%Y-%m-%d")
        return (
            f"Long-term cognition from {len(reflections)} reflections:\n"
            f"- Covers {run_count} execution cycles\n"
            f"- Distilled at: {now}\n"
            f"- Reflection summaries:\n"
            + "\n".join(f"  - {r.content[:100]}" for r in reflections[:5])
        )

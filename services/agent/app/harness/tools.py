"""工具层基础架构 — BaseTool 抽象基类 + ToolResult + ToolRegistry + TTLCache

提供 OpenAI function calling 兼容的工具注册和调用机制。
移植自 QuantHatch agent_engine，零外部依赖（纯 stdlib + asyncio）。
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable

import logging

logger = logging.getLogger("agent_engine.tools")


@dataclass
class ToolResult:
    """工具执行结果统一数据结构"""

    tool_id: str
    action: str
    success: bool
    data: list[dict] = field(default_factory=list)
    summary: str = ""
    data_source: str = ""  # "db" | "api" | "cache" | "external" 等
    error: str | None = None

    def to_prompt_text(self) -> str:
        """转为可直接拼入 LLM Prompt 的文本"""
        if not self.success:
            return f"[{self.tool_id}.{self.action}] Error: {self.error}"
        return f"[{self.tool_id}.{self.action}] (source: {self.data_source})\n{self.summary}"

    def to_dict(self) -> dict:
        """转为字典，用于 JSON 序列化"""
        return {
            "tool_id": self.tool_id,
            "action": self.action,
            "success": self.success,
            "data": self.data,
            "summary": self.summary,
            "data_source": self.data_source,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolResult":
        """从字典构造 ToolResult 实例"""
        return cls(
            tool_id=d["tool_id"],
            action=d["action"],
            success=d["success"],
            data=d.get("data", []),
            summary=d.get("summary", ""),
            data_source=d.get("data_source", ""),
            error=d.get("error"),
        )


class BaseTool(ABC):
    """工具抽象基类

    每个工具可以有多个 action，通过 tool_id 和 action 唯一标识。
    子类需要实现 _execute 方法。

    Attributes:
        tool_id: 工具唯一标识，如 "weather_query"
        tool_name: 工具显示名称
        description: 工具功能描述
        actions: 支持的动作列表
        action_schemas: 每个动作的参数 JSON Schema（用于 function calling）
        tags: 工具标签（用于分类、权限控制等）
    """

    tool_id: str = ""
    tool_name: str = ""
    description: str = ""
    actions: list[str] = []
    action_schemas: dict[str, dict] = {}
    tags: list[str] = []  # 自定义标签，如 ["read", "write", "dangerous"]

    async def execute(
        self, action: str, params: dict[str, Any], context: Any = None,
    ) -> ToolResult:
        """统一执行入口

        Args:
            action: 要执行的动作名
            params: 动作参数
            context: 执行上下文（如 DB session、用户信息等），由引擎透传
        """
        if action not in self.actions:
            return ToolResult(
                tool_id=self.tool_id,
                action=action,
                success=False,
                error=f"Unsupported action '{action}', available: {self.actions}",
            )

        start = time.monotonic()
        try:
            result = await self._execute(action, params, context)
            elapsed = time.monotonic() - start
            logger.info(
                f"[{self.tool_id}.{action}] OK "
                f"({result.data_source}, {len(result.data)} items, {elapsed:.2f}s)"
            )
            return result
        except Exception as e:
            elapsed = time.monotonic() - start
            logger.error(f"[{self.tool_id}.{action}] Error ({elapsed:.2f}s): {e}")
            return ToolResult(
                tool_id=self.tool_id,
                action=action,
                success=False,
                error=str(e),
            )

    @abstractmethod
    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None,
    ) -> ToolResult:
        """子类实现具体逻辑"""
        ...

    @staticmethod
    async def _run_sync(fn: Callable, *args, **kwargs) -> Any:
        """将同步阻塞函数放入线程池执行，避免阻塞事件循环"""
        func = partial(fn, *args, **kwargs) if args or kwargs else fn
        return await asyncio.to_thread(func)

    def _format_for_llm(self, data: list[dict], max_rows: int = 30) -> str:
        """将结构化数据格式化为 LLM 可读文本摘要"""
        if not data:
            return "No data"
        rows = data[:max_rows]
        lines: list[str] = []
        for row in rows:
            parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
            lines.append(" | ".join(parts))
        text = "\n".join(lines)
        if len(data) > max_rows:
            text += f"\n... ({len(data)} total, showing first {max_rows})"
        return text

    def get_schema(self) -> dict:
        """返回工具元信息"""
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "actions": self.actions,
            "tags": self.tags,
        }

    def to_function_definitions(self) -> list[dict]:
        """导出为 OpenAI function calling 格式的 tools 数组

        每个 action 生成一个 function，名称格式: {tool_id}__{action}
        """
        functions: list[dict] = []
        for action in self.actions:
            schema = self.action_schemas.get(action, {
                "type": "object",
                "properties": {},
                "required": [],
            })
            functions.append({
                "type": "function",
                "function": {
                    "name": f"{self.tool_id}__{action}",
                    "description": f"{self.description} — {action}",
                    "parameters": schema,
                },
            })
        return functions

    # ---- 便捷构造方法 ----

    def _ok(
        self, action: str, data: list[dict], source: str, summary: str = "",
    ) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=True,
            data=data,
            summary=summary or self._format_for_llm(data),
            data_source=source,
        )

    def _empty(self, action: str, msg: str) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=True,
            data=[],
            summary=msg,
            data_source="api",
        )

    def _fail(self, action: str, error: str) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            action=action,
            success=False,
            error=error,
        )


class ToolRegistry:
    """工具注册中心

    支持别名机制、按 ID 查找、批量导出 function definitions。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._aliases: dict[str, str] = {}  # 旧名 -> 新名
        self._write_tools: set[str] = set()  # 写操作工具集合

    def _resolve_alias(self, tool_id: str) -> str:
        """将别名解析为实际 tool_id"""
        return self._aliases.get(tool_id, tool_id)

    def register(self, tool: BaseTool) -> None:
        """注册工具实例"""
        if tool.tool_id in self._tools:
            logger.warning(f"Tool '{tool.tool_id}' already registered, overwriting")
        self._tools[tool.tool_id] = tool
        # 自动标记写工具
        if "write" in tool.tags:
            self._write_tools.add(tool.tool_id)
        logger.info(f"Registered tool: {tool.tool_id} ({tool.description})")

    def register_alias(self, old_id: str, new_id: str) -> None:
        """注册工具别名（旧 ID 自动转发到新 ID）"""
        self._aliases[old_id] = new_id

    def mark_write_tools(self, *tool_ids: str) -> None:
        """标记需要串行执行的写工具"""
        self._write_tools.update(tool_ids)

    def is_write_tool(self, tool_id: str) -> bool:
        """判断是否为写工具"""
        return self._resolve_alias(tool_id) in self._write_tools

    def get(self, tool_id: str) -> BaseTool | None:
        """按 ID 获取工具（支持别名解析）"""
        resolved = self._resolve_alias(tool_id)
        return self._tools.get(resolved)

    def list_tools(self) -> list[dict]:
        """返回所有已注册工具的元信息"""
        return [tool.get_schema() for tool in self._tools.values()]

    def get_function_definitions(
        self, tool_ids: set[str] | None = None,
    ) -> list[dict]:
        """获取指定工具集的 function calling 定义

        Args:
            tool_ids: 限定工具 ID 集合，None 表示全部
        """
        if tool_ids is not None:
            tool_ids = {self._resolve_alias(tid) for tid in tool_ids}

        defs: list[dict] = []
        seen: set[str] = set()
        for tid, tool in self._tools.items():
            if tool_ids is not None and tid not in tool_ids:
                continue
            if tid in seen:
                continue
            seen.add(tid)
            defs.extend(tool.to_function_definitions())
        return defs

    async def execute(
        self,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        context: Any = None,
    ) -> ToolResult:
        """统一调用入口（支持别名解析）"""
        resolved = self._resolve_alias(tool_id)
        tool = self._tools.get(resolved)
        if tool is None:
            return ToolResult(
                tool_id=tool_id,
                action=action,
                success=False,
                error=f"Unknown tool '{tool_id}', available: {list(self._tools.keys())}",
            )
        return await tool.execute(action, params, context)


class TTLCache:
    """简易内存 TTL 缓存"""

    def __init__(self, ttl_seconds: int = 14400) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        # 惰性清理过期项
        now = time.monotonic()
        self._store = {
            k: (ts, v) for k, (ts, v) in self._store.items()
            if now - ts <= self._ttl
        }
        return len(self._store)

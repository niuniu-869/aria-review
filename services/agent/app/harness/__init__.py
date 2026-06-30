"""Agent Engine Harness — 通用 LLM Autonomous Function Calling 框架

移植自 QuantHatch agent_engine。提供：
- 多轮 function calling 自主循环
- 三阶段上下文窗口裁剪（soft → medium → hard）
- LLM 多提供商路由和 fallback
- 工具注册和调用机制（读/写智能并发）
- 双层记忆系统（短期/长期，纯内存存储）
- 内存 Pub/Sub 实时事件

无 Redis / Celery 依赖；唯一外部依赖：httpx。
"""

__version__ = "0.1.0"

# Core engine
from .engine import (
    autonomous_loop,
    execute_tool_calls,
    trim_messages_to_fit,
    build_research_memo,
    estimate_str_tokens,
    estimate_messages_tokens,
)

# Tools
from .tools import (
    BaseTool,
    ToolResult,
    ToolRegistry,
    TTLCache,
)

# LLM
from .llm import (
    LLMRouter,
    LLMClientConfig,
    call_llm,
    call_llm_with_fallback,
)

# Memory
from .memory import (
    Memory,
    MemoryType,
    MemoryService,
    MemoryStore,
    InMemoryStore,
)

# Events
from .events import (
    EventPublisher,
    EventType,
    InMemoryEventPublisher,
    NullEventPublisher,
    publish_run_event,
)

# Config
from .config import (
    EngineConfig,
    LLMProviderConfig,
    get_config,
    set_config,
)

__all__ = [
    # Engine
    "autonomous_loop",
    "execute_tool_calls",
    "trim_messages_to_fit",
    "build_research_memo",
    "estimate_str_tokens",
    "estimate_messages_tokens",
    # Tools
    "BaseTool",
    "ToolResult",
    "ToolRegistry",
    "TTLCache",
    # LLM
    "LLMRouter",
    "LLMClientConfig",
    "call_llm",
    "call_llm_with_fallback",
    # Memory
    "Memory",
    "MemoryType",
    "MemoryService",
    "MemoryStore",
    "InMemoryStore",
    # Events
    "EventPublisher",
    "EventType",
    "InMemoryEventPublisher",
    "NullEventPublisher",
    "publish_run_event",
    # Config
    "EngineConfig",
    "LLMProviderConfig",
    "get_config",
    "set_config",
]

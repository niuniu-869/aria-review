"""Agent 工作台层 — 在 harness 引擎之上的可序列化、可暂停/恢复的 agent 运行抽象。

提供：
- AgentContext: 一次 agent 运行的静态依赖与配置（registry/llm_router/prompts/...）
- AGENT_SYSTEM / WRAP_UP: 综述 agent 的 system persona 与最终轮提示

LoopState / step_once 仍定义在 harness.engine（与循环体逻辑同居），此处仅
重新导出便于调用方从 app.agent 统一引入。
"""
from __future__ import annotations

from .context import AgentContext
from .prompts import AGENT_SYSTEM, WRAP_UP

__all__ = [
    "AgentContext",
    "AGENT_SYSTEM",
    "WRAP_UP",
]

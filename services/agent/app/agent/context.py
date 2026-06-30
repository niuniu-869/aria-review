"""AgentContext — 一次 agent 运行的静态依赖与配置容器。

把 autonomous_loop 散落的参数（registry / llm_router / prompts / tool_ids / ...）
聚成一个不可变（约定上）的上下文对象，供 step_once 在多步推进中复用。

注意：这里只放"静态依赖"（运行全程不变的东西）。运行期可变状态
（messages / round_idx / tool_results / ...）一律放进 LoopState。
deadline 等运行期派生值不进 AgentContext，由调用方在 step_once 入参显式传入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentContext:
    """一次 agent 运行的静态上下文（运行全程不变）。

    Attributes:
        registry: ToolRegistry — 工具注册中心
        llm_router: LLMRouter — LLM 路由器
        model_names: 模型优先级列表（fallback 链）
        system_prompt: 系统提示词（已写入 LoopState.messages[0]，此处留底备查）
        tool_ids: 限定可用工具集合，None 表示全部
        max_rounds: 最大工具调用轮数
        wrap_up_prompt: 最终总结轮提示词
        importance_scores: 工具重要性评分（上下文裁剪用）
        extra_tool_params: 注入每个工具调用的额外参数
        tool_context: 透传给工具执行的上下文（如 DB session）
        run_id: 当前 run 的 id（M2 幂等/确认协议用；autonomous_loop 不传）
        session_factory: async_sessionmaker，供 step_once 做写工具幂等短路
            （ToolInvocation 执行前查/执行后记）。None 表示不启用幂等审计（M1 行为）。
    """

    registry: object            # ToolRegistry
    llm_router: object          # LLMRouter
    model_names: list[str]
    system_prompt: str
    tool_ids: set[str] | None
    max_rounds: int
    wrap_up_prompt: str
    importance_scores: dict | None = None
    extra_tool_params: dict | None = None
    tool_context: Any = None
    run_id: int | None = None
    session_factory: Any = None

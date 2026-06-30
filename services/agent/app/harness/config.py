"""Agent Engine 配置管理

通过 dataclass 提供类型安全的配置，支持从环境变量或字典加载。
移植自 QuantHatch agent_engine，删除 Celery 相关字段。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class LLMProviderConfig:
    """单个 LLM 提供商配置"""
    name: str                  # 提供商标识，如 "openai", "deepseek"
    api_key: str = ""
    base_url: str = ""
    models: list[str] = field(default_factory=list)  # 该提供商支持的模型列表
    reasoning_models: set[str] = field(default_factory=set)  # 推理模型子集
    model_api_names: dict[str, str] = field(default_factory=dict)  # 内部名 -> API 名映射


@dataclass
class EngineConfig:
    """Agent Engine 全局配置"""

    # LLM 提供商列表
    llm_providers: list[LLMProviderConfig] = field(default_factory=list)

    # 默认模型（当 Agent 未指定模型时使用）
    default_model: str = "gpt-4o"

    # 上下文窗口
    context_limit: int = 128_000     # 模型最大 token 数
    context_reserve: int = 20_000    # 预留给 response + tools schema

    # 工具执行
    tool_concurrency: int = 8        # 单轮工具并发上限
    tool_result_max_chars: int = 12000 # 单条工具结果最大字符数（检索枚举需暴露 50-100 条 candidate_id 供 LLM 自筛）
    tool_timeout: int = 60           # 单个工具执行超时（秒）

    # LLM 调用
    llm_timeout: int = 180           # 普通模型超时（秒）
    llm_timeout_reasoning: int = 360 # 推理模型超时（秒）
    llm_max_retries: int = 3         # 单个模型重试次数

    # 自主循环
    loop_base_timeout: int = 120     # 基础超时（秒）
    loop_per_round_timeout: int = 90 # 每轮额外超时（秒）
    memo_interval: int = 8           # 研究备忘录插入间隔（每 N 轮）

    # 记忆系统
    memory_short_term_ttl_days: int = 30  # 短期记忆过期天数
    memory_max_context: int = 5           # 注入 prompt 的最大记忆条数
    memory_cognition_interval: int = 10   # 认知提炼间隔（每 N 次成功 Run）

    # 日志
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> EngineConfig:
        """从环境变量加载配置"""
        config = cls(
            default_model=os.getenv("DEFAULT_MODEL", "gpt-4o"),
            context_limit=int(os.getenv("CONTEXT_LIMIT", "128000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

        # 自动发现 LLM 提供商（通过 {NAME}_API_KEY 环境变量）
        # 支持 OpenAI 兼容格式
        for prefix in ("OPENAI", "DEEPSEEK", "QWEN", "KIMI", "DOUBAO", "GLM",
                        "ANTHROPIC", "GOOGLE", "MISTRAL"):
            key = os.getenv(f"{prefix}_API_KEY", "")
            if key:
                base_url = os.getenv(
                    f"{prefix}_BASE_URL",
                    "https://api.openai.com/v1" if prefix == "OPENAI" else "",
                )
                config.llm_providers.append(LLMProviderConfig(
                    name=prefix.lower(),
                    api_key=key,
                    base_url=base_url,
                ))

        return config


# 全局配置单例
_config: EngineConfig | None = None


def get_config() -> EngineConfig:
    """获取全局配置"""
    global _config
    if _config is None:
        _config = EngineConfig.from_env()
    return _config


def set_config(config: EngineConfig) -> None:
    """设置全局配置（通常在应用启动时调用或测试中覆盖）"""
    global _config
    _config = config

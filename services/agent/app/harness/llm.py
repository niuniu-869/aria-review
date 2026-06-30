"""LLM 多提供商路由 — 根据 model_id 自动选择 API 凭证

支持 OpenAI 兼容 API 格式，可扩展任意提供商。
移植自 QuantHatch agent_engine，唯一外部依赖：httpx（已在 requirements）。

阶段 2b 新增：
- per-request LLM 配置覆盖（OverrideLLMConfig）：支持用户自带 key/base_url/model
- 无 key 回退 FakeLLMClient：无任何 key 时不静默打真实 API
- stream_content()：异步逐 token 流式原语，供 Phase5 SSE 端点用
- _ensure_dotenv_loaded()：确保 services/agent/.env 在 harness 用到前已加载

TODO (Phase 3): Claude/Anthropic 原生 SDK 适配（Messages API 格式与 OpenAI 兼容层不同，
               需要 anthropic 包或自行拼 /v1/messages；此处只做 LLMRouter 注册占位）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .config import get_config, LLMProviderConfig

logger = logging.getLogger("agent_engine.llm")


# ======================================================================
# .env 加载保证
# ======================================================================

def _ensure_dotenv_loaded() -> None:
    """确保 services/agent/.env 已被加载到 os.environ。

    app/config.py 在模块导入时会通过 python-dotenv 加载 .env，
    所以只要 app.config 已被 import，这里直接 import 它触发副作用即可。
    若 app.config 不可用（独立测试场景），fallback 到直接 load_dotenv。
    这样 harness 层可以独立于 FastAPI app 使用，也不会重复加载。
    """
    if os.environ.get("_HARNESS_DOTENV_LOADED"):
        return
    try:
        # 优先复用 app.config 的加载（副作用：dotenv 已加载）
        import app.config  # noqa: F401
        os.environ["_HARNESS_DOTENV_LOADED"] = "1"
        return
    except (ImportError, Exception):
        pass
    # 独立场景回退：直接加载同目录 .env
    try:
        from dotenv import load_dotenv
        _env_path = Path(__file__).resolve().parents[2] / ".env"
        if _env_path.exists():
            load_dotenv(_env_path, override=False)
        os.environ["_HARNESS_DOTENV_LOADED"] = "1"
    except ImportError:
        pass  # python-dotenv 不可用时静默跳过（容器通常已注入环境变量）


# ======================================================================
# 错误处理工具
# ======================================================================

def _sanitize_error(e: Exception) -> str:
    """脱敏异常信息，移除可能包含凭证的内容"""
    msg = str(e) or repr(e)
    msg = re.sub(r'Bearer\s+\S+', 'Bearer <REDACTED>', msg)
    msg = re.sub(r'(?i)(api[_-]?key|authorization)[=:]\s*\S+', r'\1=<REDACTED>', msg)
    return msg[:500]


# ======================================================================
# 数据类
# ======================================================================

@dataclass(frozen=True)
class LLMClientConfig:
    """LLM 客户端配置"""
    api_key: str
    base_url: str
    model: str
    reasoning_model: bool = False


@dataclass
class OverrideLLMConfig:
    """Per-request LLM 覆盖配置（用户自带 key，不落盘）

    供 autonomous_loop 等调用点接受用户透传的凭证，
    优先级高于 .env / EngineConfig 中的配置。
    任意字段为空时，该字段回退到路由器中的默认值。
    """
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def is_empty(self) -> bool:
        return not (self.api_key or self.base_url or self.model)


# ======================================================================
# Fake LLM（无 key 回退）
# ======================================================================

class FakeLLMClient:
    """无任何 key 时的回退客户端，不发真实请求。

    - call() 返回构造的 OpenAI 兼容 response dict，可含 tool_calls（用于测试）。
    - stream() 逐 token 异步生成确定文本。

    绝不静默调用真实 API。
    """

    def __init__(
        self,
        canned_content: str | None = None,
        canned_tool_call: dict | None = None,
    ):
        """Args:
            canned_content: stream/call 返回的确定文本内容
            canned_tool_call: 若设置，call() 在第一次调用时返回此 tool_call（用于测试工具循环）
        """
        self.canned_content = canned_content or "（无 API Key，使用 Fake LLM 回退）"
        self.canned_tool_call = canned_tool_call
        self._call_count = 0

    def _build_response(self, content: str, tool_calls: list[dict] | None = None) -> dict:
        """构造 OpenAI 兼容格式的响应 dict"""
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {
            "id": "fake-completion-000",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_fake": True,
        }

    async def call(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """非流式调用：返回构造的完整 response dict（含可选 tool_calls）"""
        self._call_count += 1
        # 第一次调用且配置了 canned_tool_call → 返回 tool_call
        if self._call_count == 1 and self.canned_tool_call:
            return self._build_response("", tool_calls=[self.canned_tool_call])
        return self._build_response(self.canned_content)

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        """流式调用：逐 token 异步生成"""
        for tok in self.canned_content.split(" "):
            yield tok + " "


# ======================================================================
# LLM 路由器
# ======================================================================

class LLMRouter:
    """LLM 路由器 — 管理多提供商和模型映射

    使用方式:
        router = LLMRouter()
        router.add_provider("openai", api_key="REDACTED_API_KEY", base_url="https://api.openai.com/v1")
        router.register_model("gpt-4o", provider="openai")
        config = router.resolve("gpt-4o")
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProviderConfig] = {}
        self._model_provider_map: dict[str, str] = {}
        self._reasoning_models: set[str] = set()
        self._model_api_names: dict[str, str] = {}  # 内部名 -> API 名

    def add_provider(
        self,
        name: str,
        api_key: str,
        base_url: str,
        models: list[str] | None = None,
        reasoning_models: set[str] | None = None,
        model_api_names: dict[str, str] | None = None,
    ) -> None:
        """添加 LLM 提供商"""
        provider = LLMProviderConfig(
            name=name,
            api_key=api_key,
            base_url=base_url,
            models=models or [],
            reasoning_models=reasoning_models or set(),
            model_api_names=model_api_names or {},
        )
        self._providers[name] = provider
        # 自动注册模型映射
        for model in provider.models:
            self._model_provider_map[model] = name
        self._reasoning_models.update(provider.reasoning_models)
        self._model_api_names.update(provider.model_api_names)

    def register_model(
        self,
        model_id: str,
        provider: str,
        *,
        api_name: str | None = None,
        is_reasoning: bool = False,
    ) -> None:
        """注册单个模型到提供商的映射"""
        self._model_provider_map[model_id] = provider
        if api_name:
            self._model_api_names[model_id] = api_name
        if is_reasoning:
            self._reasoning_models.add(model_id)

    def resolve(self, model_id: str, override: "OverrideLLMConfig | None" = None) -> LLMClientConfig:
        """解析 model_id 到 API 配置，支持 per-request 覆盖

        覆盖优先级（高 → 低）：
          1. override.api_key / override.base_url / override.model
          2. LLMProviderConfig（从 EngineConfig 加载）

        Raises:
            ValueError: 未知模型或提供商未配置 API Key
        """
        # 确定实际使用的 model_id（override 可能换模型）
        effective_model = (override.model if override and override.model else model_id)

        # per-request override 已给出完整 OpenAI-compatible 连接信息时，不依赖
        # 启动期 provider 注册。这样用户可用自定义 base_url/model 临时接入代理服务。
        if override and override.api_key and override.base_url:
            return LLMClientConfig(
                api_key=override.api_key,
                base_url=override.base_url,
                model=effective_model,
                reasoning_model=False,
            )

        provider_name = self._model_provider_map.get(effective_model)
        if not provider_name:
            # 尝试原始 model_id
            provider_name = self._model_provider_map.get(model_id)
        if not provider_name:
            # 按模型名前缀推断 provider (如 deepseek-chat -> deepseek)，
            # 兼容 from_env 自动发现但未显式注册 models 列表的 provider。
            for pname in self._providers:
                if effective_model.lower().startswith(pname.lower()):
                    provider_name = pname
                    break
        if not provider_name:
            raise ValueError(f"Unknown model: {effective_model!r} (and {model_id!r})")

        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"Provider '{provider_name}' not configured")

        # 合并覆盖
        effective_api_key = (override.api_key if override and override.api_key else None) or provider.api_key
        effective_base_url = (override.base_url if override and override.base_url else None) or provider.base_url

        if not effective_api_key:
            raise ValueError(
                f"Model {effective_model} -> provider {provider_name}: API Key not set"
            )

        api_model = self._model_api_names.get(effective_model, effective_model)
        return LLMClientConfig(
            api_key=effective_api_key,
            base_url=effective_base_url,
            model=api_model,
            reasoning_model=effective_model in self._reasoning_models,
        )

    def get_available_providers(self) -> list[str]:
        """返回已配置 API Key 的提供商列表"""
        return [name for name, p in self._providers.items() if p.api_key]

    def has_any_key(self) -> bool:
        """是否有任意提供商配置了 API Key"""
        return bool(self.get_available_providers())

    @classmethod
    def from_config(cls) -> "LLMRouter":
        """从全局配置构建路由器（会先确保 .env 已加载）"""
        _ensure_dotenv_loaded()
        config = get_config()
        router = cls()
        for p in config.llm_providers:
            router.add_provider(
                name=p.name,
                api_key=p.api_key,
                base_url=p.base_url,
                models=p.models,
                reasoning_models=p.reasoning_models,
                model_api_names=p.model_api_names,
            )
        return router


# ======================================================================
# LLM 调用函数
# ======================================================================

async def call_llm(
    config: LLMClientConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: int | None = None,
    max_retries: int = 3,
) -> dict:
    """调用单个 LLM 模型（非流式），返回原始 API 响应

    Args:
        config: LLM 客户端配置
        messages: 消息列表
        tools: function calling 工具定义
        timeout: 超时秒数（None 则自动根据是否推理模型选择）
        max_retries: 重试次数

    Returns:
        OpenAI 兼容格式的 API 响应 dict
    """
    engine_config = get_config()
    if timeout is None:
        timeout = (
            engine_config.llm_timeout_reasoning
            if config.reasoning_model
            else engine_config.llm_timeout
        )

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError(f"Model {config.model}: invalid response, missing choices")

            return data
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_error = e
            logger.warning(f"[LLM] {config.model} retry {attempt}/{max_retries}: {type(e).__name__}")
            if attempt < max_retries:
                await asyncio.sleep(2.0 * attempt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 or e.response.status_code == 429:
                last_error = e
                logger.warning(f"[LLM] {config.model} retry {attempt}/{max_retries}: {e.response.status_code}")
                if attempt < max_retries:
                    await asyncio.sleep(3.0 * attempt)
            else:
                raise

    raise last_error or ValueError(f"Model {config.model}: all retries failed")


async def call_llm_with_fallback(
    router: LLMRouter,
    model_names: list[str],
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    override: OverrideLLMConfig | None = None,
) -> tuple[dict, str]:
    """按模型列表顺序尝试调用 LLM（fallback 链），支持 per-request 覆盖

    无 key 时自动回退到 FakeLLMClient，绝不静默打真实 API。

    Args:
        router: LLM 路由器
        model_names: 模型优先级列表
        messages: 消息列表
        tools: function calling 工具定义
        override: per-request LLM 配置覆盖（用户自带 key）

    Returns:
        (response_dict, model_id_used)
    """
    if not model_names:
        raise ValueError("No models specified")

    last_error: Exception | None = None
    for model_id in model_names:
        try:
            config = router.resolve(model_id, override=override)
            resp = await call_llm(config, messages, tools)
            return resp, model_id
        except ValueError as e:
            # API Key 未配置 → 不重试其他模型，直接走 Fake
            if "API Key not set" in str(e):
                logger.info(f"[LLM] {model_id}: no API key, falling back to FakeLLMClient")
                fake = FakeLLMClient()
                resp = await fake.call(messages, tools)
                return resp, f"fake:{model_id}"
            logger.warning(f"[LLM] Model {model_id} resolve failed: {_sanitize_error(e)}")
            last_error = e
        except Exception as e:
            logger.warning(f"[LLM] Model {model_id} failed: {_sanitize_error(e)}")
            last_error = e

    # 所有模型都失败，且没有任何 key → Fake 兜底
    if not router.has_any_key():
        logger.info("[LLM] No API keys configured, using FakeLLMClient")
        fake = FakeLLMClient()
        resp = await fake.call(messages, tools)
        return resp, "fake"

    raise ValueError(
        f"All models failed ({', '.join(model_names)}): {_sanitize_error(last_error)}"
    )


# ======================================================================
# 流式原语（Phase 5 SSE 端点用）
# ======================================================================

async def stream_content(
    messages: list[dict],
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    json_mode: bool = False,
    timeout: float = 120.0,
) -> AsyncIterator[str]:
    """流式生成 content token，移植自 biblio app/llm.py DeepSeekClient.stream。

    与 app/llm.py 的 DeepSeekClient 并存（后续统一时以本函数为准，
    app/llm.py 可改为薄包装调用此函数）。

    Args:
        messages: OpenAI 兼容消息列表
        api_key: API Key（必须非空，否则应改用 FakeLLMClient.stream）
        base_url: API base URL（不含路径，如 https://api.deepseek.com/v1）
        model: 模型名
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        json_mode: 是否启用 JSON 输出格式
        timeout: 总超时秒数

    Yields:
        str: 逐 token 内容增量

    Raises:
        ValueError: api_key 为空
        httpx.HTTPStatusError: API 返回非 200
    """
    if not api_key:
        raise ValueError("stream_content: api_key 不能为空，请先检查是否有 key；无 key 请用 FakeLLMClient.stream()")

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base = base_url.rstrip("/")
    url = f"{base}/chat/completions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as c:
        async with c.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code != 200:
                body_text = await r.aread()
                raise httpx.HTTPStatusError(
                    f"stream_content: API 错误 {r.status_code}: {body_text[:200]}",
                    request=r.request,
                    response=r,
                )
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

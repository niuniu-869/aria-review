"""LLM 客户端抽象 (移植自 legacy fct_llm_deepseek.R, 改为流式)。

- DeepSeekClient: OpenAI 兼容 /chat/completions, stream=true, SSE 增量。
- FakeStreamClient: 无 key 时回退, 流式吐回确定文本 (测试/本地无 key 可跑)。
安全: 永不记录 Authorization; 用户 key 仅透传, 不落盘 (沿用 v0.6 会话级思路)。
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Protocol

import httpx

from .config import settings
from .net_safety import normalize_external_url


class LLMError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class LLMClient(Protocol):
    def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...


class FakeStreamClient:
    """无 key 回退 / 测试用: 把一段确定文本按词流式吐出。"""

    def __init__(self, canned: str | None = None):
        self.canned = canned or (
            "本研究综述基于语料生成。代表性文献 [1] 指出该领域近年快速发展; "
            "Smith et al. (2020) 进一步验证。 (语料未覆盖, 需补充检索)"
        )

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        for tok in self.canned.split(" "):
            yield tok + " "

    async def complete(self, messages: list[dict], **kwargs) -> str:
        parts = [tok async for tok in self.stream(messages, **kwargs)]
        return "".join(parts).strip()


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str | None = None,
                 model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.base_url = _normalize_base_url(base_url or settings.deepseek_base_url)
        self.model = model

    async def stream(self, messages: list[dict], temperature: float = 0.3,
                     max_tokens: int = 2048, json_mode: bool = False) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if json_mode:  # DeepSeek/OpenAI 兼容 JSON 模式 (供结构化抽取)
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as c:
                async with c.stream(
                    "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
                ) as r:
                    if r.status_code != 200:
                        raise LLMError(f"LLM API 错误 {r.status_code}")
                    content_type = r.headers.get("content-type", "").lower()
                    if not any(
                        expected in content_type
                        for expected in ("text/event-stream", "application/json", "application/x-ndjson")
                    ):
                        raise LLMError("LLM API returned a non JSON/SSE response; check whether Base URL needs /v1")
                    seen_content = False
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
                            seen_content = True
                            yield delta
                    if not seen_content:
                        raise LLMError("LLM API returned no content; check Base URL/model")
        except httpx.HTTPError:
            raise LLMError("LLM 服务不可达")

    async def complete(self, messages: list[dict], temperature: float = 0.3,
                       max_tokens: int = 2048, json_mode: bool = False) -> str:
        parts = [tok async for tok in self.stream(messages, temperature=temperature,
                                                  max_tokens=max_tokens, json_mode=json_mode)]
        return "".join(parts).strip()


def _normalize_base_url(base_url: str) -> str:
    """校验并规范化 OpenAI-compatible base URL。"""
    try:
        return normalize_external_url(base_url, default_path="/v1")
    except ValueError as exc:
        raise LLMError("LLM Base URL 非法，需为 http/https URL") from exc


def get_llm_client(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """优先用户透传 key, 否则 .env, 都无则 Fake (不静默调真实 API)。"""
    key = (api_key or "").strip() or settings.deepseek_api_key.strip()
    if key:
        params = resolve_llm_params(key, base_url=base_url, model=model)
        return DeepSeekClient(params.api_key, params.base_url, params.model)
    return FakeStreamClient()


# ============================================================================
# 统一 LLM key 解析 (流式 stream_content 与非流式两路径共用)
# ============================================================================

from dataclasses import dataclass
from app.harness.llm import OverrideLLMConfig


@dataclass
class LLMParams:
    """LLM 参数包装（供 resolve_llm_params 返回）"""
    api_key: str
    base_url: str
    model: str


def resolve_llm_params(
    api_key: str | None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMParams:
    """统一 key 解析：用户透传优先，否则环境默认。

    供流式 (stream_content) 与非流式两路径共用。

    Args:
        api_key: 用户透传的 API Key（可为 None 或空串）
        base_url: 用户透传的 OpenAI-compatible Base URL（可选）
        model: 用户透传的模型名（可选）

    Returns:
        LLMParams: 包含 api_key（可能为空）、base_url、model
    """
    key = (api_key or "").strip() or settings.deepseek_api_key.strip()
    resolved_base_url = _normalize_base_url(base_url or settings.deepseek_base_url)
    resolved_model = (model or "").strip() or "deepseek-chat"
    return LLMParams(
        api_key=key,
        base_url=resolved_base_url,
        model=resolved_model
    )


def override_from_key(
    api_key: str | None,
    base_url: str | None = None,
    model: str | None = None,
) -> OverrideLLMConfig | None:
    """把 LLM 请求头转成 harness 的 OverrideLLMConfig。

    供 call_llm_with_fallback override 参数使用。
    无有效 key 时返回 None。

    Args:
        api_key: 用户透传的 API Key（可为 None 或空串）
        base_url: 用户透传的 OpenAI-compatible Base URL（可选）
        model: 用户透传的模型名（可选）

    Returns:
        OverrideLLMConfig: 若有有效 key；否则 None
    """
    p = resolve_llm_params(api_key, base_url=base_url, model=model)
    if not p.api_key:
        return None
    return OverrideLLMConfig(
        api_key=p.api_key,
        base_url=p.base_url,
        model=p.model
    )

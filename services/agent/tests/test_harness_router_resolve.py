"""回归测试: LLMRouter 按模型名前缀推断 provider。

修复背景: EngineConfig.from_env 自动发现 provider 时 models 列表为空,
导致 resolve("deepseek-chat") 报 Unknown model, e2e 的 map/reduce 退化成 FakeLLM。
修复: resolve() 在映射未命中时按模型名前缀 (deepseek-chat -> deepseek) 推断 provider。
"""
from __future__ import annotations

import pytest

from app.harness.llm import LLMRouter, OverrideLLMConfig


def _router_no_models() -> LLMRouter:
    # 模拟 from_env: provider 有 key/base_url 但 models 列表为空
    r = LLMRouter()
    r.add_provider("deepseek", api_key="test-api-key", base_url="https://api.deepseek.com/v1")
    return r


def test_resolve_infers_provider_by_prefix():
    cfg = _router_no_models().resolve("deepseek-chat")
    assert cfg.api_key == "test-api-key"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"  # 无 api_name 映射时原样下发


def test_resolve_prefix_with_override_model():
    cfg = _router_no_models().resolve(
        "gpt-4o", override=OverrideLLMConfig(model="deepseek-reasoner"))
    assert cfg.model == "deepseek-reasoner"  # override 模型同样按前缀推断到 deepseek


def test_resolve_unknown_provider_still_raises():
    with pytest.raises(ValueError, match="Unknown model"):
        _router_no_models().resolve("anthropic-claude-3")  # 无 anthropic provider

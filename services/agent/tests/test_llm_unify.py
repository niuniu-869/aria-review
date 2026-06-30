"""统一 LLM key 解析 — 流式与非流式路径共用的 helper 函数测试"""
import pytest


def test_resolve_with_user_key():
    """用户透传 key 优先"""
    from app.llm import resolve_llm_params
    p = resolve_llm_params("user-test-api-key")
    assert p.api_key == "user-test-api-key"
    assert p.model == "deepseek-chat"
    assert p.base_url  # 应该是 settings 的默认值


def test_resolve_with_custom_base_url_and_model():
    """用户透传 base_url/model 优先"""
    from app.llm import resolve_llm_params
    p = resolve_llm_params(
        "user-test-api-key",
        base_url="https://proxy.example.com/v1/",
        model="gpt-5.5",
    )
    assert p.api_key == "user-test-api-key"
    assert p.base_url == "https://proxy.example.com/v1"
    assert p.model == "gpt-5.5"


def test_resolve_with_root_base_url_adds_v1():
    """OpenAI-compatible 根 URL 自动补 /v1，适配 Sub2API 等代理。"""
    from app.llm import resolve_llm_params
    p = resolve_llm_params(
        "user-test-api-key",
        base_url="https://sub2api0.zeabur.app/",
        model="gpt-5.5",
    )
    assert p.base_url == "https://sub2api0.zeabur.app/v1"


def test_resolve_keeps_nested_v1_base_url():
    """已有业务前缀和 /v1 时不重复追加。"""
    from app.llm import resolve_llm_params
    p = resolve_llm_params("user-test-api-key", base_url="https://proxy.example.com/openai/v1/")
    assert p.base_url == "https://proxy.example.com/openai/v1"


def test_resolve_rejects_invalid_base_url():
    """非法 URL 直接失败，避免后续拼接出隐蔽请求。"""
    from app.llm import LLMError, resolve_llm_params
    with pytest.raises(LLMError):
        resolve_llm_params("user-test-api-key", base_url="proxy.example.com")


def test_resolve_rejects_private_base_url():
    """用户透传 base_url 不允许指向本机/内网，避免服务端 SSRF。"""
    from app.llm import LLMError, resolve_llm_params
    with pytest.raises(LLMError):
        resolve_llm_params("user-test-api-key", base_url="http://127.0.0.1:11434/v1")


def test_resolve_with_none_key_uses_env(monkeypatch):
    """无透传 key，从环境变量读"""
    from app.llm import resolve_llm_params
    import app.config as cfg

    # 模拟设置环境变量中的 key
    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "env-test-api-key-default", raising=False)
    p = resolve_llm_params(None)
    assert p.api_key == "env-test-api-key-default"
    assert p.model == "deepseek-chat"


def test_resolve_with_empty_string_key_uses_env(monkeypatch):
    """空串 key 视同无，从环境变量读"""
    from app.llm import resolve_llm_params
    import app.config as cfg

    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "env-test-api-key-fallback", raising=False)
    p = resolve_llm_params("")
    assert p.api_key == "env-test-api-key-fallback"
    assert p.model == "deepseek-chat"


def test_override_from_key_returns_none_when_both_empty(monkeypatch):
    """无透传 key 且无环境 key 时返回 None"""
    from app.llm import override_from_key
    import app.config as cfg

    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "", raising=False)
    o = override_from_key(None)
    assert o is None


def test_override_from_key_builds_config():
    """有 key 时构建完整的 OverrideLLMConfig"""
    from app.llm import override_from_key
    o = override_from_key("test-api-key-x")
    assert o is not None
    assert o.api_key == "test-api-key-x"
    assert o.model == "deepseek-chat"
    assert o.base_url  # 应该有默认值


def test_override_from_key_supports_custom_base_url_and_model():
    """有 key/base_url/model 时构建完整 OverrideLLMConfig"""
    from app.llm import override_from_key
    o = override_from_key("test-api-key-x", base_url="https://proxy.example.com", model="gpt-5.5")
    assert o is not None
    assert o.api_key == "test-api-key-x"
    assert o.base_url == "https://proxy.example.com/v1"
    assert o.model == "gpt-5.5"


def test_override_from_key_prefers_user_key(monkeypatch):
    """用户 key 优先于环境变量"""
    from app.llm import override_from_key
    import app.config as cfg

    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "env-test-api-key", raising=False)
    o = override_from_key("user-test-api-key")
    assert o is not None
    assert o.api_key == "user-test-api-key"


def test_llm_params_dataclass():
    """LLMParams 数据类正确包装"""
    from app.llm import LLMParams
    p = LLMParams(api_key="test-api-key", base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    assert p.api_key == "test-api-key"
    assert p.base_url == "https://api.deepseek.com/v1"
    assert p.model == "deepseek-chat"

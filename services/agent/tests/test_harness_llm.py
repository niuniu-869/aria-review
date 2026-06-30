"""阶段 2b — harness LLM 层测试

覆盖：
1. 用户 key 覆盖（OverrideLLMConfig）：mock httpx，断言 Authorization 用覆盖 key
2. 无 key → FakeLLMClient 回退，不发真实请求
3. stream_content 对 FakeLLMClient.stream 逐 token 产出
4. ★ 真实 DeepSeek 多步工具调用冒烟（opt-in，有 DEEPSEEK_API_KEY 时跑）
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import json

# 确保能找到 app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 加载 .env（让 DEEPSEEK_API_KEY 进入 os.environ）
try:
    from app.config import settings as _settings  # noqa: F401 — 触发 dotenv 加载副作用
except Exception:
    pass

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.harness.config import EngineConfig, set_config, LLMProviderConfig
from app.harness.llm import (
    LLMRouter,
    LLMClientConfig,
    OverrideLLMConfig,
    FakeLLMClient,
    call_llm,
    call_llm_with_fallback,
    stream_content,
)
from app.harness.tools import BaseTool, ToolRegistry, ToolResult
from app.harness.events import NullEventPublisher
from app.harness.engine import autonomous_loop


# ======================================================================
# 辅助工具
# ======================================================================

def _make_stub_config() -> EngineConfig:
    return EngineConfig(
        context_limit=128_000,
        context_reserve=20_000,
        tool_concurrency=4,
        tool_timeout=30,
        tool_result_max_chars=4000,
        loop_base_timeout=60,
        loop_per_round_timeout=60,
        memo_interval=8,
        llm_timeout=30,
        llm_timeout_reasoning=60,
    )


def _make_router_with_key(api_key: str, base_url: str, model: str) -> LLMRouter:
    router = LLMRouter()
    router.add_provider(
        name="deepseek",
        api_key=api_key,
        base_url=base_url,
        models=[model],
    )
    return router


def _make_router_no_key(model: str = "deepseek-chat") -> LLMRouter:
    """构造没有任何 API key 的路由器"""
    router = LLMRouter()
    router.add_provider(
        name="deepseek",
        api_key="",  # 故意空 key
        base_url="https://api.deepseek.com/v1",
        models=[model],
    )
    return router


def _openai_response(content: str = "OK", tool_calls: list | None = None) -> dict:
    """构造 OpenAI 兼容响应"""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# ======================================================================
# Fixture
# ======================================================================

@pytest.fixture(autouse=True)
def stub_config():
    set_config(_make_stub_config())
    yield
    set_config(None)


# ======================================================================
# Test 1: 用户 key 覆盖 — mock httpx，断言 Authorization 用覆盖 key
# ======================================================================

@pytest.mark.asyncio
async def test_user_key_override_used_in_request():
    """OverrideLLMConfig.api_key 应覆盖路由器中的 .env key"""
    env_key = "env-secret-key"
    override_key = "user-override-key-12345"
    model = "deepseek-chat"

    router = _make_router_with_key(env_key, "https://api.deepseek.com/v1", model)
    override = OverrideLLMConfig(api_key=override_key)

    captured_headers: dict = {}

    async def mock_post(url, *, headers=None, json=None, **kwargs):
        captured_headers.update(headers or {})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=_openai_response("done"))
        return mock_resp

    # 拦截 httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=mock_post)):
        config = router.resolve(model, override=override)
        assert config.api_key == override_key, "resolve 应返回覆盖 key"

        resp = await call_llm(config, [{"role": "user", "content": "hi"}])

    assert "Authorization" in captured_headers
    auth = captured_headers["Authorization"]
    assert auth == f"Bearer {override_key}", (
        f"HTTP 请求应使用覆盖 key，但用了: {auth}"
    )
    assert env_key not in auth, "不应使用 .env key"


# ======================================================================
# Test 2: 无 key → FakeLLMClient 回退，不发真实请求
# ======================================================================

@pytest.mark.asyncio
async def test_no_key_falls_back_to_fake_no_real_request():
    """无 API key 时 call_llm_with_fallback 应回退到 FakeLLMClient，不发真实 HTTP"""
    router = _make_router_no_key("deepseek-chat")

    real_request_made = False

    async def mock_post(*args, **kwargs):
        nonlocal real_request_made
        real_request_made = True
        raise AssertionError("不应发送真实 HTTP 请求！")

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=mock_post)):
        resp, model_used = await call_llm_with_fallback(
            router, ["deepseek-chat"], [{"role": "user", "content": "test"}]
        )

    assert not real_request_made, "不应发送真实 HTTP 请求"
    assert resp.get("_fake") is True, "应返回 Fake 响应"
    assert "fake" in model_used, f"model_used 应含 'fake'，实际: {model_used}"
    # 内容应是 FakeLLMClient 的默认文本
    content = resp["choices"][0]["message"]["content"]
    assert content, "Fake 响应应有内容"


# ======================================================================
# Test 3: FakeLLMClient.stream 逐 token 产出
# ======================================================================

@pytest.mark.asyncio
async def test_fake_stream_yields_tokens():
    """FakeLLMClient.stream 应逐 token 异步产出，完整拼接后等于原始文本"""
    canned = "hello world this is a test"
    fake = FakeLLMClient(canned_content=canned)

    tokens = []
    async for tok in fake.stream([{"role": "user", "content": "hi"}]):
        tokens.append(tok)

    assert len(tokens) > 1, "应产出多个 token"
    joined = "".join(tokens).strip()
    # 每个 tok 加了空格，strip 后应与原文相同
    assert joined == canned, f"拼接结果应等于原始文本，实际: {joined!r}"


@pytest.mark.asyncio
async def test_fake_stream_call_returns_tool_call_first_then_content():
    """FakeLLMClient 配置 canned_tool_call 时，第一次 call 返回 tool_call，第二次返回内容"""
    canned_tc = {
        "id": "call-fake-001",
        "type": "function",
        "function": {"name": "math__add", "arguments": '{"a": 2, "b": 2}'},
    }
    fake = FakeLLMClient(canned_content="结果是 4", canned_tool_call=canned_tc)

    # 第一次调用
    resp1 = await fake.call([{"role": "user", "content": "calc"}])
    tc1 = resp1["choices"][0]["message"].get("tool_calls")
    assert tc1 is not None, "第一次应含 tool_calls"
    assert tc1[0]["function"]["name"] == "math__add"

    # 第二次调用
    resp2 = await fake.call([{"role": "user", "content": "calc"}])
    tc2 = resp2["choices"][0]["message"].get("tool_calls")
    content2 = resp2["choices"][0]["message"].get("content", "")
    assert not tc2, "第二次不应含 tool_calls"
    assert "4" in content2


# ======================================================================
# Test 4: stream_content 对 Fake 的等价实现（mock SSE 响应）
# ======================================================================

@pytest.mark.asyncio
async def test_stream_content_yields_tokens_from_mock_sse():
    """stream_content 应解析 SSE 响应并逐 token yield"""
    tokens_to_send = ["Hello", " world", "!"]

    def make_sse_lines():
        for i, tok in enumerate(tokens_to_send):
            chunk = json.dumps({
                "choices": [{"delta": {"content": tok}, "index": 0}]
            })
            yield f"data: {chunk}\n"
        yield "data: [DONE]\n"

    # 构造 mock streaming response
    class MockStreamResponse:
        status_code = 200
        request = MagicMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_lines(self):
            for line in make_sse_lines():
                yield line.rstrip()

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return MockStreamResponse()

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        collected = []
        async for tok in stream_content(
            [{"role": "user", "content": "hi"}],
            api_key="test-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        ):
            collected.append(tok)

    assert collected == tokens_to_send, f"应收到 {tokens_to_send}，实际: {collected}"


@pytest.mark.asyncio
async def test_stream_content_raises_on_empty_api_key():
    """stream_content 传入空 api_key 时应立即抛 ValueError，不发请求"""
    with pytest.raises(ValueError, match="api_key"):
        async for _ in stream_content(
            [{"role": "user", "content": "hi"}],
            api_key="",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        ):
            pass


# ======================================================================
# Test 5: OverrideLLMConfig 覆盖 base_url
# ======================================================================

@pytest.mark.asyncio
async def test_override_base_url():
    """OverrideLLMConfig.base_url 应覆盖路由器中的 base_url"""
    router = _make_router_with_key("test-api-key-env", "https://api.deepseek.com/v1", "deepseek-chat")
    override = OverrideLLMConfig(base_url="https://my-proxy.example.com/v1")

    config = router.resolve("deepseek-chat", override=override)
    assert config.base_url == "https://my-proxy.example.com/v1"
    assert config.api_key == "test-api-key-env"  # 未覆盖的字段保持原值


# ======================================================================
# Test 6: ★ 真实 DeepSeek 多步工具调用冒烟（有 key 时跑）
# ======================================================================

_DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# deepseek-chat 支持 function calling；deepseek-v4-flash 不一定支持，优先用 deepseek-chat
_SMOKE_MODEL = "deepseek-chat"

_has_key = bool(_DEEPSEEK_API_KEY)


class MathAddTool(BaseTool):
    """冒烟测试工具：math__add(a, b) → a+b"""
    tool_id = "math"
    tool_name = "Math Tool"
    description = "执行数学运算"
    actions = ["add"]
    action_schemas = {
        "add": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个数"},
                "b": {"type": "number", "description": "第二个数"},
            },
            "required": ["a", "b"],
        }
    }
    tags = []

    def __init__(self):
        self.call_log: list[dict] = []

    async def _execute(self, action: str, params: dict, context=None) -> ToolResult:
        self.call_log.append({"action": action, "params": params})
        if action == "add":
            result = params["a"] + params["b"]
            return ToolResult(
                tool_id=self.tool_id,
                action=action,
                success=True,
                data=[{"result": result}],
                summary=f"{params['a']} + {params['b']} = {result}",
                data_source="local",
            )
        return ToolResult(tool_id=self.tool_id, action=action, success=False, error="未知操作")


@pytest.mark.skipif(not _has_key, reason="无 DEEPSEEK_API_KEY，跳过真实冒烟测试")
@pytest.mark.asyncio
async def test_real_deepseek_tool_call_smoke():
    """★ 真实 DeepSeek 多步工具调用冒烟

    使用 .env 中的 DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL，
    模型：deepseek-chat（支持 function calling）。

    断言：
    1. math__add 工具被真实调用（参数 a=2, b=2）
    2. 最终答案含 "4"
    3. loop 正常结束（未触发 max_rounds 兜底）
    """
    math_tool = MathAddTool()
    registry = ToolRegistry()
    registry.register(math_tool)

    router = LLMRouter()
    router.add_provider(
        name="deepseek",
        api_key=_DEEPSEEK_API_KEY,
        base_url=_DEEPSEEK_BASE_URL,
        models=[_SMOKE_MODEL],
    )

    # 独立配置（不影响全局 stub_config fixture，这里覆盖一次）
    smoke_config = EngineConfig(
        context_limit=32_000,
        context_reserve=4_000,
        tool_concurrency=2,
        tool_timeout=30,
        tool_result_max_chars=2000,
        loop_base_timeout=120,
        loop_per_round_timeout=90,
        memo_interval=8,
        llm_timeout=60,
        llm_timeout_reasoning=120,
    )
    set_config(smoke_config)

    t0 = time.time()

    final_text, model_used, tool_results, rounds_log = await autonomous_loop(
        registry=registry,
        llm_router=router,
        model_names=[_SMOKE_MODEL],
        system_prompt=(
            "You are a helpful assistant. When asked to compute something, "
            "you MUST use the math__add tool to perform the calculation. "
            "Do NOT compute in your head; call the tool."
        ),
        user_prompt="请使用 math__add 工具计算 2+2，然后告诉我结果是多少。",
        max_rounds=5,
        publisher=NullEventPublisher(),
    )

    elapsed = round(time.time() - t0, 2)

    # ---- 断言 ----
    assert math_tool.call_log, (
        f"★ 风险闸 BLOCKED: math__add 工具未被真实调用！"
        f" model={model_used}, rounds={len(rounds_log)}, final={final_text[:200]}"
    )

    called_add = any(log["action"] == "add" for log in math_tool.call_log)
    assert called_add, f"add action 未被调用，call_log={math_tool.call_log}"

    # 检查参数（允许 LLM 传 int 或 float）
    add_call = next(log for log in math_tool.call_log if log["action"] == "add")
    assert float(add_call["params"].get("a", 0)) == 2.0
    assert float(add_call["params"].get("b", 0)) == 2.0

    # 最终答案含 "4"
    assert "4" in final_text, (
        f"最终答案未含 '4'，实际: {final_text[:300]}"
    )

    # loop 正常结束（未撞 max_rounds 极限）
    total_rounds = len(rounds_log)
    assert total_rounds <= 5, f"轮数超限: {total_rounds}"

    # 打印冒烟摘要（-s 可见）
    tool_round = next((r for r in rounds_log if not r.get("is_final")), None)
    final_round = next((r for r in rounds_log if r.get("is_final")), None)
    print(f"\n[SMOKE] model={model_used}, elapsed={elapsed}s, rounds={total_rounds}")
    print(f"[SMOKE] tool_call_log={math_tool.call_log}")
    print(f"[SMOKE] final_text={final_text[:300]!r}")

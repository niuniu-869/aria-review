"""ToolResult 序列化/反序列化测试"""
import json
import pytest


def test_tool_result_roundtrip():
    """测试 to_dict 和 from_dict 往返一致性"""
    from app.harness.tools import ToolResult

    r = ToolResult(
        tool_id="library",
        action="add",
        success=True,
        data=[{"id": 1}],
        summary="ok",
        data_source="db",
        error=None
    )
    d = r.to_dict()

    # 验证 to_dict 输出
    assert isinstance(d, dict)
    assert d["tool_id"] == "library"
    assert d["action"] == "add"
    assert d["success"] is True
    assert d["data"] == [{"id": 1}]
    assert d["summary"] == "ok"
    assert d["data_source"] == "db"
    assert d["error"] is None

    # 验证 from_dict 往返
    r2 = ToolResult.from_dict(d)
    assert r2.tool_id == r.tool_id
    assert r2.action == r.action
    assert r2.success == r.success
    assert r2.data == r.data
    assert r2.summary == r.summary
    assert r2.data_source == r.data_source
    assert r2.error is None


def test_tool_result_json_safe():
    """测试 to_dict 输出可安全 JSON 序列化"""
    from app.harness.tools import ToolResult

    r = ToolResult(
        tool_id="x",
        action="y",
        success=False,
        data=[],
        summary="",
        data_source="",
        error="boom"
    )

    # 应该不抛异常
    json_str = json.dumps(r.to_dict())
    assert isinstance(json_str, str)

    # 验证往返
    d = json.loads(json_str)
    r2 = ToolResult.from_dict(d)
    assert r2.tool_id == "x"
    assert r2.action == "y"
    assert r2.success is False
    assert r2.error == "boom"


def test_tool_result_from_dict_with_missing_fields():
    """测试 from_dict 处理缺失字段（使用默认值）"""
    from app.harness.tools import ToolResult

    # 最小化的 dict（只有必填字段）
    minimal = {
        "tool_id": "test_tool",
        "action": "test_action",
        "success": True,
    }

    r = ToolResult.from_dict(minimal)
    assert r.tool_id == "test_tool"
    assert r.action == "test_action"
    assert r.success is True
    assert r.data == []  # 默认值
    assert r.summary == ""  # 默认值
    assert r.data_source == ""  # 默认值
    assert r.error is None  # 默认值


def test_tool_result_from_dict_with_all_fields():
    """测试 from_dict 处理完整字段"""
    from app.harness.tools import ToolResult

    complete = {
        "tool_id": "db",
        "action": "query",
        "success": True,
        "data": [{"name": "Alice", "age": 30}],
        "summary": "Found 1 record",
        "data_source": "postgres",
        "error": None,
    }

    r = ToolResult.from_dict(complete)
    assert r.tool_id == "db"
    assert r.action == "query"
    assert r.success is True
    assert r.data == [{"name": "Alice", "age": 30}]
    assert r.summary == "Found 1 record"
    assert r.data_source == "postgres"
    assert r.error is None

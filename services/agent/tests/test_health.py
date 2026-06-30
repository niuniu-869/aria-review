import time

import pytest

from app.main import _r_health_last_ok, _R_HEALTH_GRACE


@pytest.fixture(autouse=True)
def _reset_r_health_cache():
    # grace 缓存是模块级全局，逐测试重置避免跨用例污染（codex P2）。
    _r_health_last_ok["ts"] = 0.0
    yield
    _r_health_last_ok["ts"] = 0.0


def test_health_ok_r_up(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["service"] == "agent"
    assert b["rService"] == "up"


def test_health_r_down_no_recent_ok(client, fake_r):
    # R 从未健康过(或 grace 已过期) → 如实报 down
    _r_health_last_ok["ts"] = 0.0
    fake_r.up = False
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["rService"] == "down"


def test_health_r_down_within_grace_reports_up(client, fake_r):
    # R 近期健康过(检索期短暂阻塞健康探针) → grace 内仍报 up, 避免前端误报"部分不可用"
    fake_r.up = True
    client.get("/healthz")           # 记录一次成功 → _r_health_last_ok 更新
    fake_r.up = False
    r = client.get("/healthz")       # 立即失败但在 grace 窗口内
    assert r.json()["rService"] == "up"


def test_health_r_down_after_grace_reports_down(client, fake_r):
    # 持续失败超过 grace 窗口 → 如实报 down(真正宕机仍能暴露)
    _r_health_last_ok["ts"] = time.monotonic() - (_R_HEALTH_GRACE + 5)
    fake_r.up = False
    r = client.get("/healthz")
    assert r.json()["rService"] == "down"


def test_request_id_header_present(client):
    r = client.get("/healthz")
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert "x-request-id" in headers and headers["x-request-id"]


def test_request_id_propagated(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert headers["x-request-id"] == "abc-123"

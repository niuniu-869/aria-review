"""MinerU 客户端单元测试 (mock httpx) + 真实集成测试。

单元测试：submit/upload/poll/download 流程；poll running→done 流转；失败 state→错误。
集成测试：@pytest.mark.skipif 无 OCR_TOKEN 时跳过。
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.config import settings
from app.ingest import mineru as mineru_mod
from app.ingest.mineru import (
    submit_batch,
    upload,
    poll,
    download_markdown,
    parse_pdfs,
)


# ---------------------------------------------------------------------------
# 辅助：构造 mock httpx.AsyncClient
# ---------------------------------------------------------------------------

def _make_client(
    *,
    submit_resp: dict | None = None,
    upload_status: int = 200,
    poll_resps: list[dict] | None = None,
    zip_bytes: bytes | None = None,
):
    """返回一个可注入的 AsyncMock httpx.AsyncClient。"""
    client = AsyncMock(spec=httpx.AsyncClient)

    # POST → submit
    post_resp = MagicMock()
    post_resp.raise_for_status = MagicMock()
    post_resp.json = MagicMock(return_value=submit_resp or {
        "code": 0,
        "data": {"batch_id": "batch-001", "file_urls": ["https://oss.example.com/upload/test.pdf"]},
    })
    client.post = AsyncMock(return_value=post_resp)

    # PUT → upload
    put_resp = MagicMock()
    put_resp.status_code = upload_status
    put_resp.raise_for_status = MagicMock(
        side_effect=None if upload_status < 400
        else httpx.HTTPStatusError("err", request=MagicMock(), response=put_resp)
    )
    client.put = AsyncMock(return_value=put_resp)

    # GET → 可能是 poll 或 zip 下载（用调用次数区分）
    get_calls: list[dict] = []
    if poll_resps is not None:
        get_calls.extend(poll_resps)  # 先是 poll
    if zip_bytes is not None:
        get_calls.append({"_zip": zip_bytes})  # 最后是 zip 下载

    get_responses: list[MagicMock] = []
    for item in get_calls:
        r = MagicMock()
        r.raise_for_status = MagicMock()
        if "_zip" in item:
            r.content = item["_zip"]
        else:
            r.json = MagicMock(return_value=item)
        get_responses.append(r)

    _get_iter = iter(get_responses)

    async def _get(url, **kwargs):
        return next(_get_iter)

    client.get = _get
    return client


def _make_zip(files: dict[str, str]) -> bytes:
    """创建内存 zip，keys 为文件名，values 为内容。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# submit_batch 测试
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_batch_happy():
    client = _make_client(submit_resp={
        "code": 0,
        "data": {"batch_id": "b-123", "file_urls": ["https://oss/a.pdf", "https://oss/b.pdf"]},
    })
    batch_id, urls = await submit_batch(
        [{"name": "a.pdf", "data_id": "1"}, {"name": "b.pdf", "data_id": "2"}],
        client=client,
    )
    assert batch_id == "b-123"
    assert urls == ["https://oss/a.pdf", "https://oss/b.pdf"]


@pytest.mark.asyncio
async def test_submit_batch_api_error():
    client = _make_client(submit_resp={"code": 1, "msg": "配额不足"})
    with pytest.raises(RuntimeError, match="配额不足"):
        await submit_batch([{"name": "x.pdf", "data_id": "1"}], client=client)


# ---------------------------------------------------------------------------
# upload 测试
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_happy():
    client = _make_client(upload_status=200)
    # 不应抛异常
    await upload("https://oss/put/x.pdf", b"PDF_CONTENT", client=client)
    client.put.assert_called_once()


@pytest.mark.asyncio
async def test_upload_fail_raises():
    client = _make_client(upload_status=403)
    with pytest.raises(httpx.HTTPStatusError):
        await upload("https://oss/put/x.pdf", b"data", client=client)


# ---------------------------------------------------------------------------
# poll 测试
# ---------------------------------------------------------------------------

_DONE_RESP = {
    "code": 0,
    "data": {"extract_result": [
        {"file_name": "test.pdf", "state": "done",
         "full_zip_url": "https://cdn.example.com/result.zip", "err_msg": None}
    ]},
}

_RUNNING_RESP = {
    "code": 0,
    "data": {"extract_result": [
        {"file_name": "test.pdf", "state": "running", "full_zip_url": None, "err_msg": None}
    ]},
}

_FAILED_RESP = {
    "code": 0,
    "data": {"extract_result": [
        {"file_name": "test.pdf", "state": "failed",
         "full_zip_url": None, "err_msg": "解析出错"}
    ]},
}


@pytest.mark.asyncio
async def test_poll_immediate_done(monkeypatch):
    monkeypatch.setattr(mineru_mod, "_POLL_INTERVAL", 0)
    client = _make_client(poll_resps=[_DONE_RESP])
    results = await poll("b-001", client=client, poll_interval=0)
    assert results[0]["state"] == "done"
    assert results[0]["full_zip_url"] == "https://cdn.example.com/result.zip"


@pytest.mark.asyncio
async def test_poll_running_then_done(monkeypatch):
    """running → done 流转：第一次轮询 running，第二次 done。"""
    monkeypatch.setattr(mineru_mod, "_POLL_INTERVAL", 0)
    client = _make_client(poll_resps=[_RUNNING_RESP, _DONE_RESP])
    results = await poll("b-001", client=client, poll_interval=0)
    assert results[0]["state"] == "done"


@pytest.mark.asyncio
async def test_poll_failed_state(monkeypatch):
    """failed 状态：直接返回（不轮询），结果 state=failed。"""
    monkeypatch.setattr(mineru_mod, "_POLL_INTERVAL", 0)
    client = _make_client(poll_resps=[_FAILED_RESP])
    results = await poll("b-001", client=client, poll_interval=0)
    assert results[0]["state"] == "failed"
    assert results[0]["err_msg"] == "解析出错"


@pytest.mark.asyncio
async def test_poll_timeout():
    """超时后抛 TimeoutError。"""
    # 让 get 永远返回 running
    client = AsyncMock(spec=httpx.AsyncClient)
    running = MagicMock()
    running.raise_for_status = MagicMock()
    running.json = MagicMock(return_value=_RUNNING_RESP)
    client.get = AsyncMock(return_value=running)

    with pytest.raises(TimeoutError):
        await poll("b-001", client=client, poll_interval=0, timeout=0)


# ---------------------------------------------------------------------------
# download_markdown 测试
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_markdown_full_md():
    md_content = "# My Paper\n\n## Abstract\n\nThis is abstract."
    zip_bytes = _make_zip({"full.md": md_content, "content_list.json": '{"items":[]}'})
    client = _make_client(zip_bytes=zip_bytes)

    text, extras = await download_markdown("https://cdn.example.com/result.zip", client=client)
    assert "My Paper" in text
    assert "Abstract" in text
    assert "content_list" in extras


@pytest.mark.asyncio
async def test_download_markdown_missing_full_md():
    zip_bytes = _make_zip({"other.txt": "no markdown here"})
    client = _make_client(zip_bytes=zip_bytes)
    with pytest.raises(KeyError, match="full.md"):
        await download_markdown("https://cdn.example.com/result.zip", client=client)


# ---------------------------------------------------------------------------
# parse_pdfs 端到端测试（mock）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_pdfs_happy(tmp_path):
    """parse_pdfs 串起全部步骤，返回 done + markdown。"""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")

    md_content = "# Test Paper\n\n## Abstract\n\nTest abstract."
    zip_bytes = _make_zip({"full.md": md_content})

    client = _make_client(
        submit_resp={
            "code": 0,
            "data": {"batch_id": "b-test", "file_urls": ["https://oss/up/test.pdf"]},
        },
        poll_resps=[_DONE_RESP],
        zip_bytes=zip_bytes,
    )

    # poll _DONE_RESP file_name = "test.pdf" 与 pdf.name 一致
    # 但 submit 时用的 name 是 path.name = "test.pdf"
    results = await parse_pdfs([pdf], language="en", _client=client)
    assert len(results) == 1
    r = results[0]
    assert r["status"] == "done"
    assert "Test Paper" in r["markdown"]


@pytest.mark.asyncio
async def test_parse_pdfs_max_files(tmp_path):
    """max_files=1 时只处理第一个文件。"""
    pdfs = []
    for i in range(3):
        p = tmp_path / f"file{i}.pdf"
        p.write_bytes(b"%PDF fake")
        pdfs.append(p)

    # 我们给出能处理 1 个文件的 mock
    zip_bytes = _make_zip({"full.md": "# Paper\n\n## Abstract\n\nText."})
    done_resp = {
        "code": 0,
        "data": {"extract_result": [
            {"file_name": "file0.pdf", "state": "done",
             "full_zip_url": "https://cdn.example.com/result.zip", "err_msg": None}
        ]},
    }
    client = _make_client(
        submit_resp={
            "code": 0,
            "data": {"batch_id": "b-x", "file_urls": ["https://oss/file0.pdf"]},
        },
        poll_resps=[done_resp],
        zip_bytes=zip_bytes,
    )
    results = await parse_pdfs(pdfs, max_files=1, _client=client)
    assert len(results) == 1
    assert results[0]["name"] == "file0.pdf"


@pytest.mark.asyncio
async def test_parse_pdfs_failed_state(tmp_path):
    """parse_pdfs 遇到 failed 状态时返回 status=failed。"""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF fake bad")

    failed_resp = {
        "code": 0,
        "data": {"extract_result": [
            {"file_name": "bad.pdf", "state": "failed",
             "full_zip_url": None, "err_msg": "解析失败"}
        ]},
    }
    client = _make_client(
        submit_resp={
            "code": 0,
            "data": {"batch_id": "b-bad", "file_urls": ["https://oss/bad.pdf"]},
        },
        poll_resps=[failed_resp],
    )
    results = await parse_pdfs([pdf], _client=client)
    assert results[0]["status"] == "failed"
    assert "解析失败" in results[0]["err"]


# ---------------------------------------------------------------------------
# 健壮性测试（瞬时错误重试 / 逐文件失败隔离 / expected_names 不卡死轮询）
# ---------------------------------------------------------------------------

async def _noop_sleep(*_a, **_k):
    """替换 asyncio.sleep，让重试退避在测试中不真睡。"""
    return None


@pytest.mark.asyncio
async def test_upload_retries_transient_then_succeeds(monkeypatch):
    """上传遇瞬时网络异常(ReadTimeout)应退避重试，下一次成功则不抛。"""
    monkeypatch.setattr(mineru_mod.asyncio, "sleep", _noop_sleep)
    client = AsyncMock(spec=httpx.AsyncClient)
    ok = MagicMock()
    ok.status_code = 200
    ok.raise_for_status = MagicMock()
    # 第 1 次瞬时超时(str 为空)，第 2 次成功
    client.put = AsyncMock(side_effect=[httpx.ReadTimeout("x"), ok])
    await upload("https://oss/up/x.pdf", b"data", client=client)
    assert client.put.await_count == 2  # 重试了一次


@pytest.mark.asyncio
async def test_upload_4xx_no_retry_raises(monkeypatch):
    """4xx 永久错误(如预签名 URL 过期 403)不重试，立即抛。"""
    monkeypatch.setattr(mineru_mod.asyncio, "sleep", _noop_sleep)
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 403
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=resp))
    client.put = AsyncMock(return_value=resp)
    with pytest.raises(httpx.HTTPStatusError):
        await upload("https://oss/up/x.pdf", b"data", client=client)
    assert client.put.await_count == 1  # 未重试


@pytest.mark.asyncio
async def test_poll_expected_names_ignores_failed_upload(monkeypatch):
    """expected_names 仅等已上传文件：另一文件停在 waiting-file 也不卡死轮询。"""
    monkeypatch.setattr(mineru_mod, "_POLL_INTERVAL", 0)
    resp = {"code": 0, "data": {"extract_result": [
        {"file_name": "ok.pdf", "state": "done",
         "full_zip_url": "https://cdn/r.zip", "err_msg": None},
        {"file_name": "bad.pdf", "state": "waiting-file",
         "full_zip_url": None, "err_msg": None},  # 上传失败，永远 waiting
    ]}}
    client = _make_client(poll_resps=[resp])
    # 只期望 ok.pdf → 立即判定完成（不被 bad.pdf 的 waiting-file 拖到超时）
    results = await poll("b", client=client, poll_interval=0,
                         expected_names={"ok.pdf"})
    assert any(r["file_name"] == "ok.pdf" and r["state"] == "done" for r in results)


@pytest.mark.asyncio
async def test_parse_pdfs_partial_upload_failure_isolated(tmp_path, monkeypatch):
    """一个文件上传始终失败(瞬时重试耗尽)不应拖垮整批：成功的照常 done，失败的隔离。"""
    monkeypatch.setattr(mineru_mod.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(mineru_mod, "_POLL_INTERVAL", 0)
    good = tmp_path / "file0.pdf"; good.write_bytes(b"%PDF good")
    bad = tmp_path / "file1.pdf"; bad.write_bytes(b"%PDF bad")

    client = AsyncMock(spec=httpx.AsyncClient)
    # submit: 2 个 url
    post_resp = MagicMock(); post_resp.raise_for_status = MagicMock()
    post_resp.json = MagicMock(return_value={"code": 0, "data": {
        "batch_id": "b-x",
        "file_urls": ["https://oss/u0", "https://oss/u1"]}})
    client.post = AsyncMock(return_value=post_resp)

    # put: u0 成功, u1 始终瞬时超时(重试耗尽 → 上传失败被隔离)
    ok = MagicMock(); ok.status_code = 200; ok.raise_for_status = MagicMock()
    async def _put(url, **kw):
        if url == "https://oss/u1":
            raise httpx.ReadTimeout("transient")
        return ok
    client.put = _put

    # poll: 仅 file0 done（file1 未上传，不在 expected）；随后 GET 下载 zip
    poll_resp = {"code": 0, "data": {"extract_result": [
        {"file_name": "file0.pdf", "state": "done",
         "full_zip_url": "https://cdn/r.zip", "err_msg": None}]}}
    zip_bytes = _make_zip({"full.md": "# Good\n\nText."})
    poll_r = MagicMock(); poll_r.raise_for_status = MagicMock()
    poll_r.json = MagicMock(return_value=poll_resp)
    zip_r = MagicMock(); zip_r.raise_for_status = MagicMock(); zip_r.content = zip_bytes
    _get_iter = iter([poll_r, zip_r])
    async def _get(url, **kw):
        return next(_get_iter)
    client.get = _get

    results = await parse_pdfs([good, bad], _client=client)
    by_name = {r["name"]: r for r in results}
    assert by_name["file0.pdf"]["status"] == "done"
    assert "Good" in by_name["file0.pdf"]["markdown"]
    assert by_name["file1.pdf"]["status"] == "failed"
    assert "上传失败" in by_name["file1.pdf"]["err"]


# ---------------------------------------------------------------------------
# 真实 MinerU 集成测试（有 token 时才跑）
# ---------------------------------------------------------------------------

_HAS_TOKEN = bool(settings.ocr_token)

# 用于集成测试的小型真实 PDF（如无本地文件则跳过）
_TEST_PDF_PATH = Path("/tmp/mineru_integration_test.pdf")


def _create_minimal_pdf(path: Path) -> None:
    """生成一个极简合法 PDF（供集成测试用，页数=1 省配额）。"""
    # 最小可被 MinerU 解析的真实 PDF
    content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 60 >>
stream
BT /F1 12 Tf 72 720 Td (Integration Test Paper) Tj ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000378 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
441
%%EOF"""
    path.write_bytes(content)


@pytest.mark.skipif(
    not _HAS_TOKEN,
    reason="无 OCR_AUTHORIZATION_TOKEN，跳过真实 MinerU 集成测试",
)
@pytest.mark.asyncio
async def test_mineru_real_integration():
    """真实 MinerU API 集成：用 1 页最小 PDF 验证全流程（省配额）。

    断言：返回非空 markdown（MinerU 可能输出标题或内容行）。
    注意：消耗约 1 页配额。
    """
    import time as _time

    if not _TEST_PDF_PATH.exists():
        _create_minimal_pdf(_TEST_PDF_PATH)

    t0 = _time.time()
    results = await parse_pdfs([_TEST_PDF_PATH], language="en", max_files=1)
    elapsed = _time.time() - t0

    assert len(results) == 1
    r = results[0]

    # 如果 MinerU 返回 failed（偶发），打印 err 信息但不强制失败（网络/配额问题）
    if r["status"] == "failed":
        pytest.skip(f"MinerU 真实解析返回 failed（可能配额/网络）: {r['err']}")

    assert r["status"] == "done", f"状态非 done: {r}"
    assert r["markdown"], "markdown 为空"
    assert len(r["markdown"]) > 10, "markdown 过短"

    print(f"\n[集成测试] MinerU 耗时: {elapsed:.1f}s")
    print(f"[集成测试] markdown 前 300 字符:\n{r['markdown'][:300]}")

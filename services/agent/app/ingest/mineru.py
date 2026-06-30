"""MinerU v4 API 客户端 (async, httpx)。

三步流程：
  1. submit_batch() — POST /file-urls/batch → batch_id + 预签名 OSS URLs
  2. upload()       — PUT 预签名 URL 上传文件二进制（无 Bearer 头）
  3. poll()         — GET /extract-results/batch/{batch_id} 轮询，间隔 10s，超时 15min
  4. download_markdown() — GET full_zip_url 下载 zip，提取根目录 full.md

高层入口：parse_pdfs(paths, language="en") → list[dict]
  批量提交 → 并行上传 → 轮询 → 下载 markdown，返回每文件结果。
"""
from __future__ import annotations

import asyncio
import io
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_POLL_INTERVAL = 10        # 秒，轮询间隔
_POLL_TIMEOUT = 900        # 秒，最多 15 分钟
_PENDING_STATES = {"pending", "running", "converting", "waiting-file"}  # 继续轮询的状态集合
_NAME_MAX = 120            # MinerU 文件名长度上限（留余量）

# 健壮性参数（批量上传/轮询容错，避免一次瞬时错误毁掉整批）
_UPLOAD_CONCURRENCY = 5    # 上传并发上限（信号量）：N 个文件不一次性全并发，防撞 IP 限频/耗尽连接
_UPLOAD_RETRIES = 3        # 单文件上传瞬时错误（超时/连接重置）重试次数
_POLL_QUERY_RETRIES = 3    # 单次轮询 GET 瞬时错误重试次数（不影响整体 deadline）
# 可重试的瞬时网络异常：httpx.TransportError 已涵盖 TimeoutException（连接/读/池超时）+
# NetworkError（连接重置等）。这类异常的 str() 常为空——正是"批次失败:"后面空白的元凶。
_TRANSIENT_EXC = httpx.TransportError
# 批级共享 client 的连接池上限与分段超时（生产用；测试注入自带 mock）
_BATCH_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)
_BATCH_TIMEOUT = httpx.Timeout(60.0, connect=15.0, read=120.0, write=300.0, pool=30.0)


def _safe_name(p) -> str:
    """生成 MinerU 可接受的安全文件名（≤120 字符，**保留原扩展名**）。

    MinerU 2.5+ 支持多格式（.pdf/.docx/.pptx/.html 等，§0.6），按扩展名判文件类型。
    名字超长时只截断 stem，**保留原始 suffix**——绝不强制改成 .pdf，否则 .docx/.html
    会被误判为「unsupported file type」（且批量提交原子，一个坏名整批全失败）。

    Args:
        p: pathlib.Path（或有 .name/.stem/.suffix 的对象）。
    """
    name = p.name
    if len(name) <= _NAME_MAX:
        return name
    suffix = p.suffix  # 含点，如 ".pdf" / ".docx"；无扩展名时为 ""
    keep = _NAME_MAX - len(suffix)
    # 兜底：suffix 本身超长（罕见）时 keep 为负，stem[:负数]+suffix 仍含完整 suffix，
    # 返回值必超 _NAME_MAX。退化为对整名直接截断到 _NAME_MAX，保证返回长度始终 ≤上限。
    if keep <= 0:
        return name[:_NAME_MAX]
    return p.stem[:keep] + suffix


# ---------------------------------------------------------------------------
# 低层 API 方法
# ---------------------------------------------------------------------------

async def submit_batch(
    files: list[dict[str, Any]],
    language: str = "en",
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, list[str]]:
    """步骤1：提交批量解析请求。

    Args:
        files: 每项 {"name": str, "data_id": str}（is_ocr 固定 True）。
        language: "en" / "ch"。
        client: 可选注入 client（测试 mock 用）。

    Returns:
        (batch_id, file_urls)  — file_urls 与 files 一一对应。

    Raises:
        RuntimeError: API 返回 code != 0 或 HTTP 非 2xx。
    """
    body = {
        "enable_formula": True,
        "enable_table": True,
        "language": language,
        "model_version": "pipeline",
        "files": [
            {"name": f["name"], "is_ocr": True, "data_id": f["data_id"]}
            for f in files
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.ocr_token}",
        "Content-Type": "application/json",
    }

    async def _do(c: httpx.AsyncClient) -> tuple[str, list[str]]:
        # 429 速率限制 / 5xx 退避重试 (MinerU 有 QPS 限制, 批量连发易撞 429)
        last_err: Exception | None = None
        for attempt in range(1, 6):
            resp = await c.post(
                f"{settings.mineru_base_url}/file-urls/batch",
                json=body,
                headers=headers,
                timeout=60,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(
                    f"MinerU submit_batch HTTP {resp.status_code} (第 {attempt} 次重试)")
                await asyncio.sleep(min(3 * attempt, 30))
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("code", -1) != 0:
                raise RuntimeError(f"MinerU submit_batch 失败: {data.get('msg', data)}")
            batch_id: str = data["data"]["batch_id"]
            file_urls: list[str] = data["data"]["file_urls"]
            return batch_id, file_urls
        raise last_err or RuntimeError("MinerU submit_batch: 429 重试耗尽")

    if client is not None:
        return await _do(client)
    async with httpx.AsyncClient() as c:
        return await _do(c)


async def upload(
    url: str,
    content: bytes,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """步骤2：PUT 文件二进制到预签名 OSS URL（无 Authorization 头）。

    Args:
        url: 预签名 PUT URL。
        content: 文件字节内容。
        client: 可选注入 client。

    Raises:
        httpx.HTTPStatusError: 上传失败（4xx 永久错误，如预签名 URL 过期 403）。
        httpx.TransportError: 瞬时错误重试耗尽后仍失败。

    瞬时错误（超时/连接重置/5xx）退避重试 _UPLOAD_RETRIES 次；4xx 永久错误立即抛
    （重试无意义）。批量场景由调用方做逐文件隔离，单文件失败不毁整批。
    """
    async def _do(c: httpx.AsyncClient) -> None:
        last_err: Exception | None = None
        for attempt in range(1, _UPLOAD_RETRIES + 1):
            try:
                resp = await c.put(url, content=content, timeout=300)
            except _TRANSIENT_EXC as e:
                last_err = e
                if attempt < _UPLOAD_RETRIES:
                    await asyncio.sleep(min(2 * attempt, 10))
                    continue
                raise
            # 5xx 视为瞬时（OSS 偶发）→ 重试；4xx 永久 → raise_for_status 抛出
            if resp.status_code >= 500 and attempt < _UPLOAD_RETRIES:
                last_err = RuntimeError(f"upload HTTP {resp.status_code}")
                await asyncio.sleep(min(2 * attempt, 10))
                continue
            resp.raise_for_status()
            return
        if last_err:  # 理论不可达（循环内已 raise/return），防御性
            raise last_err

    if client is not None:
        return await _do(client)
    async with httpx.AsyncClient(timeout=_BATCH_TIMEOUT) as c:
        return await _do(c)


async def poll(
    batch_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    poll_interval: float = _POLL_INTERVAL,
    timeout: float = _POLL_TIMEOUT,
    expected_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """步骤3：轮询提取结果直到所有（期望的）文件结束（done/failed）或超时。

    Args:
        batch_id: submit_batch 返回的 batch_id。
        client: 可选注入 client（测试 mock 用）。
        poll_interval: 轮询间隔秒数（测试时可设短）。
        timeout: 最大等待秒数（默认 900s = 15min）。
        expected_names: 仅等待这些文件名达到终态。用于排除"上传失败"的文件——它们
            在 OSS 无内容会永远停在 waiting-file，若不排除会把整批轮询拖到超时。
            None（默认/兼容旧调用）时等待返回结果中的全部文件。

    Returns:
        extract_result 列表，每项含 {file_name, state, full_zip_url, err_msg}。

    Raises:
        TimeoutError: 超出 timeout 仍有文件未结束。
        RuntimeError: API 返回 code != 0。
    """
    url = f"{settings.mineru_base_url}/extract-results/batch/{batch_id}"
    headers = {"Authorization": f"Bearer {settings.ocr_token}"}
    deadline = time.monotonic() + timeout

    async def _query(c: httpx.AsyncClient) -> list[dict[str, Any]]:
        # 单次轮询 GET 瞬时错误重试（一次读超时不应让整批失败）；非瞬时错误直接抛
        last_err: Exception | None = None
        for attempt in range(1, _POLL_QUERY_RETRIES + 1):
            try:
                resp = await c.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code", -1) != 0:
                    raise RuntimeError(f"MinerU poll 失败: {data.get('msg', data)}")
                return data["data"]["extract_result"]
            except _TRANSIENT_EXC as e:
                last_err = e
                if attempt < _POLL_QUERY_RETRIES:
                    await asyncio.sleep(min(2 * attempt, 10))
                    continue
                raise
        raise last_err or RuntimeError("MinerU poll: 重试耗尽")  # 防御性

    def _all_done(results: list[dict[str, Any]]) -> bool:
        if expected_names is None:
            return all(r.get("state") not in _PENDING_STATES for r in results)
        # 仅看期望文件：需全部出现且都达终态（上传失败的文件不在 expected，不阻塞）
        relevant = [r for r in results if r.get("file_name") in expected_names]
        present = {r.get("file_name") for r in relevant}
        return expected_names.issubset(present) and all(
            r.get("state") not in _PENDING_STATES for r in relevant
        )

    async def _poll_loop(c: httpx.AsyncClient) -> list[dict[str, Any]]:
        while True:
            results = await _query(c)
            if _all_done(results):
                return results
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"MinerU poll 超时 ({timeout}s)，batch_id={batch_id!r}"
                )
            await asyncio.sleep(poll_interval)

    if client is not None:
        return await _poll_loop(client)
    async with httpx.AsyncClient(timeout=_BATCH_TIMEOUT) as c:
        return await _poll_loop(c)


async def download_markdown(
    zip_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, dict[str, Any]]:
    """步骤4：下载 full_zip_url，提取 full.md 文本 + 可选元信息。

    Args:
        zip_url: CDN 直链（无需 Bearer）。
        client: 可选注入 client。

    Returns:
        (markdown_text, extras)  — extras 含 content_list（若存在）。

    Raises:
        KeyError: zip 中不含 full.md。
        RuntimeError: HTTP 非 2xx。
    """
    async def _do(c: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
        resp = await c.get(zip_url, timeout=300, follow_redirects=True)
        resp.raise_for_status()
        raw = resp.content

        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            # full.md 在 zip 根目录
            md_candidates = [n for n in names if n == "full.md" or n.endswith("/full.md")]
            if not md_candidates:
                raise KeyError(f"zip 中未找到 full.md，现有文件: {names[:20]}")
            md_name = md_candidates[0]
            markdown_text = zf.read(md_name).decode("utf-8", errors="replace")

            extras: dict[str, Any] = {}
            # 可选：content_list.json
            cl_candidates = [n for n in names if "content_list.json" in n]
            if cl_candidates:
                import json
                try:
                    extras["content_list"] = json.loads(
                        zf.read(cl_candidates[0]).decode("utf-8", errors="replace")
                    )
                except Exception:
                    pass

        return markdown_text, extras

    if client is not None:
        return await _do(client)
    async with httpx.AsyncClient() as c:
        return await _do(c)


# ---------------------------------------------------------------------------
# 高层入口
# ---------------------------------------------------------------------------

async def parse_pdfs(
    paths: list[Path | str],
    language: str = "en",
    max_files: int = 200,
    *,
    _client: httpx.AsyncClient | None = None,  # 测试注入
) -> list[dict[str, Any]]:
    """批量解析 PDF，串起全部步骤，返回每文件结果。

    Args:
        paths: PDF 文件路径列表（超出 max_files 的截断）。
        language: "en" / "ch"。
        max_files: 配额保护上限（默认 200，日配额 1000 页）。
        _client: 测试用注入 httpx.AsyncClient。

    Returns:
        list of dicts，每项:
          {
            "name": str,           # 文件名
            "path": str,           # 原始路径
            "status": "done"|"failed",
            "markdown": str|None,  # done 时有内容
            "content_list": list|None,  # done 时透传 MinerU content_list(结构/页码/bbox), 供落库 DocumentStructure; 缺失为 None
            "err": str|None,       # failed 时有错误信息
          }
    """
    paths = [Path(p) for p in paths][:max_files]
    if not paths:
        return []

    # 准备 files 列表
    ts = int(time.time())
    # data_id ≤128 字符 (MinerU 限制): 用短 id (时间戳+序号)。
    # name 截断时**必须保留原扩展名**(.pdf/.docx/.pptx/.html...): MinerU 按扩展名判
    # 类型, 截掉/篡改扩展名会报 "unsupported file type", 且批量提交原子——一个坏名整批
    # 全失败。_safe_name 已提到模块级（见上），供多格式 ingest 复用与单测。
    file_descs = [
        {"name": _safe_name(p), "data_id": f"{ts}_{i}"}
        for i, p in enumerate(paths)
    ]

    # 批级共享 client：生产用一个带连接池上限/分段超时的 client 串起 submit/upload/poll/
    # download（替代旧版每次上传新建 client → N 个文件 N 个连接池，易耗尽/撞限频）。
    # 测试注入 _client 时直接复用，不新建、不关闭。
    owns_client = _client is None
    client = _client or httpx.AsyncClient(limits=_BATCH_LIMITS, timeout=_BATCH_TIMEOUT)
    try:
        # 步骤1：提交（submit_batch 自带 429/5xx 退避重试）
        batch_id, file_urls = await submit_batch(file_descs, language, client=client)

        # 步骤2：并发上限上传 + 逐文件失败隔离（return_exceptions）。
        # 旧版裸 gather 任一失败即整批抛空消息异常 → 全标失败（含已成功的）。
        sem = asyncio.Semaphore(_UPLOAD_CONCURRENCY)

        async def _upload_one(path: Path, url: str) -> None:
            async with sem:
                content = path.read_bytes()  # 读文件失败(FileNotFoundError 等)亦被隔离
                await upload(url, content, client=client)

        outcomes = await asyncio.gather(
            *[_upload_one(p, u) for p, u in zip(paths, file_urls)],
            return_exceptions=True,
        )
        upload_errs: dict[str, str] = {}
        uploaded_names: set[str] = set()
        for fd, outcome in zip(file_descs, outcomes):
            if isinstance(outcome, BaseException):
                # repr 保留异常类型——瞬时网络异常 str() 常为空，仅 str 会得到空白错误
                upload_errs[fd["name"]] = f"上传失败: {outcome!r}"
            else:
                uploaded_names.add(fd["name"])

        # 全部上传失败 → 无需轮询，直接逐文件返回失败（不再因一处异常拖垮全部）
        if not uploaded_names:
            return [
                {"name": fd["name"], "path": str(p), "status": "failed",
                 "markdown": None, "err": upload_errs.get(fd["name"], "上传失败")}
                for p, fd in zip(paths, file_descs)
            ]

        # 步骤3：轮询（仅等已上传文件达终态，避免上传失败文件卡死轮询到超时）
        extract_results = await poll(
            batch_id, client=client, expected_names=uploaded_names,
        )

        # 步骤4：逐文件处理（done→下载 markdown；上传失败/解析失败→隔离记录）
        name_to_result = {r["file_name"]: r for r in extract_results}
        results: list[dict[str, Any]] = []
        for path, fd in zip(paths, file_descs):
            name = fd["name"]
            # 上传阶段就失败的文件：直接记录其上传错误
            if name in upload_errs:
                results.append({
                    "name": name, "path": str(path),
                    "status": "failed", "markdown": None, "err": upload_errs[name],
                })
                continue

            er = name_to_result.get(name)
            if er is None:
                results.append({
                    "name": name, "path": str(path),
                    "status": "failed", "markdown": None,
                    "err": "轮询结果中未找到该文件",
                })
                continue

            state = er.get("state", "failed")
            if state == "done":
                zip_url = er.get("full_zip_url", "")
                if not zip_url:
                    results.append({
                        "name": name, "path": str(path),
                        "status": "failed", "markdown": None,
                        "err": "state=done 但 full_zip_url 为空",
                    })
                    continue
                try:
                    markdown, extras = await download_markdown(zip_url, client=client)
                    results.append({
                        "name": name, "path": str(path),
                        "status": "done", "markdown": markdown,
                        "content_list": extras.get("content_list"),  # 新增：透传结构,供落库 DocumentStructure
                        "err": None,
                    })
                except Exception as exc:
                    results.append({
                        "name": name, "path": str(path),
                        "status": "failed", "markdown": None,
                        "err": f"下载 markdown 失败: {exc!r}",
                    })
            else:
                results.append({
                    "name": name, "path": str(path),
                    "status": "failed", "markdown": None,
                    "err": er.get("err_msg") or f"state={state}",
                })

        return results
    finally:
        if owns_client:
            await client.aclose()

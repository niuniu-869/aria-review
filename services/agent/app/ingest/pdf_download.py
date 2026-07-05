"""PDF 安全下载模块 (§4.5, codex P0) —— 信任闸门前置。

`ingest_pdf` 只接受本地 path，不负责远程下载。本模块把候选的 OA 直链**安全**地
落成本地已校验 PDF，再交给 MinerU 解析。承重项：SSRF / 每跳重定向重校验 / 大小上限 /
魔数校验 / 配额闸门。只对**入选**候选运行 (不对全部候选跑)。

reject 而非 raise：远程下载失败是常态 (OA 命中率非满)，调用方据 reason 跳过即可。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from ..config import settings
from ..net_safety import resolve_public_pinned

logger = logging.getLogger("agent.ingest.pdf_download")

_PDF_MAGIC = b"%PDF-"
_MAX_REDIRECTS = 5
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")


@dataclass
class PdfResolveResult:
    """resolve_pdf 结果。ok=True 时 path 为本地已校验 PDF；否则 reject_reason 说明原因。"""

    ok: bool
    path: str | None = None
    sha256: str | None = None
    size: int | None = None
    source_url: str | None = None
    content_type: str | None = None
    reject_reason: str | None = None

    @classmethod
    def reject(cls, reason: str, url: str | None = None) -> "PdfResolveResult":
        return cls(ok=False, reject_reason=reason, source_url=url)


def _looks_like_html(head: bytes) -> bool:
    prefix = head[:512].lstrip().lower()
    return any(prefix.startswith(m) for m in _HTML_MARKERS)


def _pin_request(current: str) -> tuple[str, dict, dict]:
    """把 URL 连接绑定到已校验的公网 IP，返回 (ip_url, headers, extensions)。

    连接目标改成 IP 字面量 → httpx 不再对 hostname 二次解析 (关 DNS rebinding TOCTOU)；
    Host 头 + TLS SNI 仍用原 hostname，保证虚拟主机路由与证书校验正确。
    """
    host, port, scheme, ip = resolve_public_pinned(current)
    parsed = urlparse(current)
    ip_host = f"[{ip}]" if ":" in ip else ip  # IPv6 需方括号
    ip_url = urlunparse((scheme, f"{ip_host}:{port}", parsed.path or "/",
                         parsed.params, parsed.query, ""))
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    host_hdr = f"[{host}]" if ":" in host else host  # IPv6 literal Host 头也要方括号
    headers = {"Host": host_hdr if default_port else f"{host_hdr}:{port}"}
    extensions = {"sni_hostname": host} if scheme == "https" else {}
    return ip_url, headers, extensions


async def _stream_download(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
) -> tuple[bytes, str]:
    """手动跟随重定向 (每跳重解析+校验+IP 绑定)，流式下载并强制大小上限。

    返回 (body_bytes, content_type)。违规抛 ValueError (SSRF/超大/重定向过多)。
    每跳都 DNS 解析 + 全 IP 公网校验 + 绑定已校验 IP (阻塞的 getaddrinfo 丢线程池)。
    """
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        ip_url, pin_headers, extensions = await asyncio.to_thread(_pin_request, current)
        async with client.stream(
            "GET", ip_url, headers=pin_headers, extensions=extensions, follow_redirects=False,
        ) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少 Location")
                # 相对跳转基于**原 hostname URL** (current) 解析，下一跳再重新绑定 IP。
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise ValueError(f"下载返回 HTTP {resp.status_code}")
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            # content-length 预检：显式超限直接拒，省流量。
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError(f"content-length {declared} 超过上限 {max_bytes}")
            total = 0
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"下载字节数超过上限 {max_bytes}")
                chunks.append(chunk)
            return b"".join(chunks), content_type
    raise ValueError(f"重定向超过 {_MAX_REDIRECTS} 跳")


async def resolve_pdf(
    pdf_url: str,
    *,
    dest_dir: str | None = None,
    max_bytes: int | None = None,
    timeout: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> PdfResolveResult:
    """把远程 OA 直链安全下载为本地已校验 PDF。

    闸门顺序：URL 公网校验 → 流式下载 (每跳重定向重校验 + 大小上限) → %PDF 魔数 +
    HTML/登录页拒绝 → 落盘 + sha256。任一步失败返回 reject (不抛)。
    """
    url = (pdf_url or "").strip()
    if not url:
        return PdfResolveResult.reject("空 URL")
    max_bytes = int(max_bytes or settings.pdf_download_max_bytes)
    timeout_s = float(timeout or settings.pdf_download_timeout)

    # 初始 URL 严格公网校验 (DNS 解析 + 拒内网，不受 env 旁路；非 http/带凭据 → 直接拒)。
    # 真正连接时 _stream_download 会每跳重解析+绑定 IP；此处仅快速预检省得建 client。
    try:
        await asyncio.to_thread(resolve_public_pinned, url)
    except ValueError as exc:
        return PdfResolveResult.reject(f"URL 不安全: {exc}", url)

    close = False
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            follow_redirects=False,  # 我们手动跟随并每跳校验
            # 禁 keep-alive 连接复用 (codex P1)：连接池按 IP origin 复用，若两个原域名
            # 解析到同一 IP，可能复用上一跳用别的 SNI 建的 TLS 连接 → 证书未按当前域名
            # 重校验。max_keepalive=0 强制每跳新建连接，SNI/证书每次重新生效。
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        close = True
    try:
        body, content_type = await _stream_download(client, url, max_bytes=max_bytes)
    except ValueError as exc:
        return PdfResolveResult.reject(str(exc), url)
    except httpx.HTTPError as exc:
        return PdfResolveResult.reject(f"下载失败: {exc}", url)
    finally:
        if close:
            await client.aclose()

    # 落盘前内容校验：魔数 + 非 HTML/登录页。
    if not body:
        return PdfResolveResult.reject("下载内容为空", url)
    if content_type == "text/html" or _looks_like_html(body):
        return PdfResolveResult.reject("返回 HTML/登录页而非 PDF", url)
    if not body.startswith(_PDF_MAGIC):
        return PdfResolveResult.reject("内容非 PDF (魔数不匹配)", url)

    sha256 = hashlib.sha256(body).hexdigest()
    dest_dir = dest_dir or os.path.join(settings.corpora_dir, "downloads")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{sha256}.pdf")
    # 已存在同 sha 文件则复用 (幂等，省重复写)。
    if not os.path.exists(path):
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=dest_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)  # 原子落盘
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return PdfResolveResult(
        ok=True,
        path=path,
        sha256=sha256,
        size=len(body),
        source_url=url,
        content_type=content_type or "application/pdf",
    )

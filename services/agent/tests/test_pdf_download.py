"""M2 PDF 安全下载模块测试 (§4.5, codex P0)。

安全矩阵：内网/凭据 URL 拒绝 / 每跳重定向重校验 / 大小上限 / %PDF 魔数 / HTML 登录页拒绝。
"""
from __future__ import annotations

import socket

import httpx
import pytest

from app import net_safety
from app.ingest.pdf_download import resolve_pdf
from app.net_safety import assert_public_url_resolved

_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
_PUBLIC_IP = "93.184.216.34"  # example.com 的真实公网 IP


def _fake_getaddrinfo(mapping: dict[str, str]):
    """构造假 getaddrinfo：host→IP。未在 mapping 的 host 默认解析到公网 IP。"""
    def _inner(host, port, *args, **kwargs):
        ip = mapping.get(host, _PUBLIC_IP)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]
    return _inner


@pytest.fixture
def public_dns(monkeypatch):
    """把所有测试域名解析到公网 IP（MockTransport 只拦 HTTP 不拦 DNS，须打桩）。"""
    monkeypatch.setattr(net_safety.socket, "getaddrinfo", _fake_getaddrinfo({}))


def _client(handler) -> httpx.AsyncClient:
    # follow_redirects=False：resolve_pdf 自行手动跟随并每跳校验。
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_rejects_private_host_url():
    r = await resolve_pdf("http://169.254.169.254/latest/meta-data")
    assert r.ok is False and "不安全" in r.reject_reason


async def test_rejects_localhost_and_credential_url():
    assert (await resolve_pdf("http://localhost:8000/x.pdf")).ok is False
    assert (await resolve_pdf("http://user:pass@example.org/x.pdf")).ok is False


async def test_rejects_non_http_scheme():
    assert (await resolve_pdf("file:///etc/passwd")).ok is False
    assert (await resolve_pdf("ftp://example.org/x.pdf")).ok is False


async def test_accepts_real_pdf(tmp_path, public_dns):
    def handler(request):
        return httpx.Response(200, content=_PDF_BYTES, headers={"content-type": "application/pdf"})

    r = await resolve_pdf("https://oa.example.org/paper.pdf", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is True
    assert r.path and r.path.endswith(".pdf")
    assert r.sha256 and r.size == len(_PDF_BYTES)
    with open(r.path, "rb") as f:
        assert f.read().startswith(b"%PDF-")


async def test_rejects_html_login_page(tmp_path, public_dns):
    html = b"<!DOCTYPE html><html><head><title>Login</title></head><body>Sign in</body></html>"

    def handler(request):
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    r = await resolve_pdf("https://paywall.example.org/paper", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is False and "HTML" in r.reject_reason


async def test_rejects_non_pdf_magic(tmp_path, public_dns):
    def handler(request):
        # content-type 谎称 pdf，但字节不是 %PDF → 魔数校验拦截。
        return httpx.Response(200, content=b"NOTAPDF blah blah",
                             headers={"content-type": "application/pdf"})

    r = await resolve_pdf("https://x.example.org/fake.pdf", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is False and "魔数" in r.reject_reason


async def test_rejects_oversize_by_content_length(tmp_path, public_dns):
    def handler(request):
        return httpx.Response(200, content=_PDF_BYTES,
                             headers={"content-type": "application/pdf", "content-length": "999999999"})

    r = await resolve_pdf("https://x.example.org/big.pdf", dest_dir=str(tmp_path),
                          max_bytes=1024, client=_client(handler))
    assert r.ok is False and "上限" in r.reject_reason


async def test_rejects_oversize_by_streamed_bytes(tmp_path, public_dns):
    big = b"%PDF-" + b"A" * 5000

    def handler(request):
        # 不声明 content-length，靠流式字节计数拦截。
        return httpx.Response(200, content=big, headers={"content-type": "application/pdf"})

    r = await resolve_pdf("https://x.example.org/big.pdf", dest_dir=str(tmp_path),
                          max_bytes=1024, client=_client(handler))
    assert r.ok is False and "上限" in r.reject_reason


def _host_header(request) -> str:
    # IP 绑定后 request.url.host 是 IP，虚拟主机名在 Host 头 (SNI 也是它)。
    return request.headers.get("host", "").split(":")[0]


async def test_redirect_to_private_host_is_rejected(tmp_path, public_dns):
    def handler(request):
        # 初始公网 URL 302 跳转到内网 → 下一跳重校验必须拦截 (SSRF 防线)。
        if _host_header(request) == "oa.example.org":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/secret.pdf"})
        return httpx.Response(200, content=_PDF_BYTES)

    r = await resolve_pdf("https://oa.example.org/redir.pdf", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is False and ("不安全" in r.reject_reason or "非公网" in r.reject_reason)


async def test_redirect_to_public_pdf_is_followed(tmp_path, public_dns):
    def handler(request):
        if _host_header(request) == "oa.example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.net/real.pdf"})
        return httpx.Response(200, content=_PDF_BYTES, headers={"content-type": "application/pdf"})

    r = await resolve_pdf("https://oa.example.org/redir.pdf", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is True and r.source_url == "https://oa.example.org/redir.pdf"


async def test_empty_url_rejected():
    assert (await resolve_pdf("")).ok is False


# --------------------------------------------------------------------------
# codex P1: DNS 解析 SSRF + env 旁路隔离 (assert_public_url_resolved)
# --------------------------------------------------------------------------

def test_resolved_validator_rejects_domain_resolving_to_private(monkeypatch):
    # 公网格式域名解析到内网 IP → 必须拒 (DNS rebinding 面)。
    monkeypatch.setattr(net_safety.socket, "getaddrinfo",
                        _fake_getaddrinfo({"evil.example.com": "127.0.0.1"}))
    with pytest.raises(ValueError):
        assert_public_url_resolved("https://evil.example.com/x.pdf")


def test_resolved_validator_accepts_domain_resolving_to_public(monkeypatch):
    monkeypatch.setattr(net_safety.socket, "getaddrinfo",
                        _fake_getaddrinfo({"ok.example.com": "8.8.8.8"}))
    assert assert_public_url_resolved("https://ok.example.com/x.pdf") == "https://ok.example.com/x.pdf"


def test_resolved_validator_ignores_private_allow_env(monkeypatch):
    # BIBLIOCN_ALLOW_PRIVATE_API_BASE_URLS 开着也不能放行内网 PDF 下载 (闸门隔离)。
    monkeypatch.setenv("BIBLIOCN_ALLOW_PRIVATE_API_BASE_URLS", "1")
    with pytest.raises(ValueError):
        assert_public_url_resolved("http://169.254.169.254/latest")  # IP 字面量内网
    with pytest.raises(ValueError):
        assert_public_url_resolved("http://10.0.0.5/x.pdf")


def test_pin_request_brackets_ipv6_host_header():
    from app.ingest.pdf_download import _pin_request
    # example.com 的公网 IPv6 literal URL：Host 头与连接 URL 都要方括号。
    ip_url, headers, ext = _pin_request(
        "https://[2606:2800:220:1:248:1893:25c8:1946]:8443/x.pdf")
    assert headers["Host"] == "[2606:2800:220:1:248:1893:25c8:1946]:8443"
    assert ip_url.startswith("https://[2606:2800:220:1:248:1893:25c8:1946]:8443/")
    assert ext["sni_hostname"] == "2606:2800:220:1:248:1893:25c8:1946"


async def test_resolve_pdf_rejects_domain_resolving_to_private(tmp_path, monkeypatch):
    monkeypatch.setattr(net_safety.socket, "getaddrinfo",
                        _fake_getaddrinfo({"rebind.example.com": "169.254.169.254"}))

    def handler(request):
        return httpx.Response(200, content=_PDF_BYTES)

    r = await resolve_pdf("https://rebind.example.com/x.pdf", dest_dir=str(tmp_path),
                          client=_client(handler))
    assert r.ok is False and "不安全" in r.reject_reason

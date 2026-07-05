"""Network safety helpers for user-provided upstream URLs."""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse, urlunparse


_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}


def _private_urls_allowed() -> bool:
    return os.environ.get("BIBLIOCN_ALLOW_PRIVATE_API_BASE_URLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_public_host(host: str) -> None:
    hostname = host.strip().strip("[]").lower()
    if not hostname:
        raise ValueError("URL 缺少 host")
    if _private_urls_allowed():
        return
    if hostname in _BLOCKED_HOSTS or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise ValueError("URL host 指向本机或局域网名称")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ValueError("URL host 必须是可公开解析的域名")
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("URL host 指向非公网地址")


def _reject_if_not_public_ip(ip_str: str) -> None:
    """非公网 IP 即拒。用 is_global 精确表达"公网可路由"——自动覆盖 private/loopback/
    link-local/CGNAT(100.64/10)/reserved/unspecified/文档段 (codex P2 补 CGNAT 漏网)；
    multicast 显式再拒一次。"""
    ip = ipaddress.ip_address(ip_str)
    if not ip.is_global or ip.is_multicast:
        raise ValueError(f"host 指向非公网地址 {ip_str}")


def resolve_public_pinned(raw_url: str) -> tuple[str, int, str, str]:
    """PDF 安全下载专用 SSRF 闸门 (codex P1)：严格公网校验 + 返回**已校验的 IP** 供连接绑定。

    返回 (host, port, scheme, pinned_ip)。调用方须连接到 pinned_ip (Host 头/SNI 用 host)，
    使 httpx 不再对 hostname 二次解析——彻底关闭 DNS rebinding 的 TOCTOU (校验时返公网、
    连接时返内网)。三处硬要求：
      1. 解析 A/AAAA，**所有**结果都必须公网 (is_global)，任一内网即拒；
      2. **不受 BIBLIOCN_ALLOW_PRIVATE_API_BASE_URLS 旁路影响** (该 env 只给内部 API base URL)；
      3. IP 字面量直接校验。
    非法/内网/凭据/无法解析 → ValueError。阻塞 (getaddrinfo)，调用方应 to_thread。
    """
    value = (raw_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL 非法，需为 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许携带用户名或密码")
    host = (parsed.hostname or "").strip().strip("[]")
    if not host:
        raise ValueError("URL 缺少 host")
    if host in _BLOCKED_HOSTS or host.lower().endswith((".localhost", ".local")):
        raise ValueError("URL host 指向本机或局域网名称")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # IP 字面量：直接校验并绑定它。
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        _reject_if_not_public_ip(host)
        return host, port, parsed.scheme, host
    # 域名：解析所有 A/AAAA，全部校验公网，绑定首个 (皆已校验，连哪个都安全)。
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"host 无法解析: {exc}") from exc
    ips = [info[4][0] for info in infos]
    if not ips:
        raise ValueError("host 无法解析到任何地址")
    for ip_str in ips:
        _reject_if_not_public_ip(ip_str)
    return host, port, parsed.scheme, ips[0]


def assert_public_url_resolved(raw_url: str) -> str:
    """同 resolve_public_pinned 的校验语义，但只返回原 URL (不绑定 IP)。用于初始快速预检。"""
    resolve_public_pinned(raw_url)
    return (raw_url or "").strip()


def assert_public_http_url(raw_url: str) -> str:
    """校验一个**完整** http(s) URL 指向公网，且不携带凭据。

    与 normalize_external_url 不同：不 rstrip path、不改写，用于 PDF 安全下载**每跳**
    重定向后对新 URL 重新校验 host (§4.5：只校验初始 URL 不够，重定向可跳进内网)。
    非法/内网/凭据 → 抛 ValueError；合法则原样返回。
    """
    value = (raw_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL 非法，需为 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许携带用户名或密码")
    _validate_public_host(parsed.hostname or "")
    return value


def normalize_external_url(raw_url: str, *, default_path: str | None = None) -> str:
    """Normalize an external http(s) URL and reject private-network targets by default."""
    value = (raw_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL 非法，需为 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许携带用户名或密码")
    _validate_public_host(parsed.hostname or "")
    if default_path and parsed.path in ("", "/"):
        parsed = parsed._replace(path=default_path)
    return urlunparse(parsed).rstrip("/")

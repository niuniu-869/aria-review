"""Network safety helpers for user-provided upstream URLs."""
from __future__ import annotations

import ipaddress
import os
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

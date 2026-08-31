"""出站 HTTP 安全网关。

本工具会按配置访问多个外部 API（行情 / 搜索 / LLM）。所有服务端出站请求必须经过
`validate_url()` 校验：
- 仅允许 http/https；
- 解析后拒绝 localhost、环回、私有（RFC1918/CGNAT）、链路本地与保留地址段，
  防止 SSRF 把内网地址当数据源。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}

_DENIED_REASONS: list[str] = []


class UnsafeURLError(ValueError):
    """URL 未通过出站安全校验。"""


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise UnsafeURLError(f"禁止访问保留/私有地址: {ip}")


def validate_url(url: str) -> str:
    """校验并原样返回合法 URL；不合法抛 UnsafeURLError。"""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("空 URL")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"仅允许 http/https, 得到 {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("缺少主机名")
    if host.lower() in BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"禁止访问 {host}")

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as exc:
        raise UnsafeURLError(f"DNS 解析失败: {host}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            _check_ip(ipaddress.ip_address(addr))
        except UnsafeURLError as exc:
            raise UnsafeURLError(str(exc)) from exc
    return url.strip()


def is_safe_url(url: str) -> bool:
    try:
        validate_url(url)
        return True
    except UnsafeURLError:
        return False


# ---------------------------------------------------------------- HTTP helpers

import json as _json  # noqa: E402
import time  # noqa: E402

import requests  # noqa: E402

USER_AGENT = "investlab/2.0 (+local research tool)"


def http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15,
    retries: int = 1,
) -> requests.Response:
    """GET 出站请求（带安全校验 + 重试）。"""
    url = validate_url(url)
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            return resp
        except requests.RequestException as exc:  # 网络错误重试
            last_exc = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def http_get_json(url: str, *, default=None, strict: bool = False, **kw):
    """GET 并解析 JSON。默认失败返回 default（数据源回退链用）；
    strict=True 时失败/非2xx 抛 RuntimeError，供需要区分
    「无结果」与「不可达」的调用方使用（如社媒热度 source_status）。"""
    try:
        resp = http_get(url, **kw)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        if strict:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise RuntimeError(f"HTTP {status or 'ERR'}") from exc
        return default


def http_post_json(
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    timeout: float = 60,
    retries: int = 1,
) -> dict:
    """POST JSON 出站请求，返回解析后的 JSON。失败抛异常。"""
    url = validate_url(url)
    hdrs = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url, data=_json.dumps(payload), headers=hdrs, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]

"""外网 HTTP 客户端：本地有 Clash 代理时走代理，服务器无代理时直连。"""

from __future__ import annotations

import os

import httpx


def outbound_proxy() -> str | None:
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def http_client(**kwargs) -> httpx.Client:
    """创建外网客户端。trust_env=False，仅使用项目 .env 写入的代理，避免 shell 里坏的 socks 代理。"""
    kwargs.setdefault("trust_env", False)
    proxy = outbound_proxy()
    if proxy and "proxy" not in kwargs:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)

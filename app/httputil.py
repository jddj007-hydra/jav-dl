from __future__ import annotations

import httpx

from app.config import Settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def client_kwargs(settings: Settings, *, use_proxy: bool) -> dict:
    kwargs: dict = {
        "follow_redirects": True,
        "timeout": settings.http_timeout,
        "headers": {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        "verify": settings.verify_tls,
    }
    if use_proxy and settings.proxy_enabled and settings.proxy_url:
        kwargs["proxy"] = settings.proxy_url
    return kwargs


def site_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(**client_kwargs(settings, use_proxy=True))


def direct_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(**client_kwargs(settings, use_proxy=False))

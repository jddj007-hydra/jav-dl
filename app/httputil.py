from __future__ import annotations

import asyncio

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


class _Held:
    """Context manager that borrows a shared client and leaves it open."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc) -> bool:
        return False


class ClientPool:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient | None] = {"site": None, "image": None}
        self._keys: dict[str, tuple | None] = {"site": None, "image": None}

    def _signature(self, settings: Settings, follow: bool) -> tuple:
        proxy = settings.proxy_url if settings.proxy_enabled and settings.proxy_url else ""
        return (proxy, bool(settings.verify_tls), float(settings.http_timeout), follow)

    def _open(self, kind: str, settings: Settings, follow: bool) -> httpx.AsyncClient:
        key = self._signature(settings, follow)
        current = self._clients[kind]
        maker = httpx.AsyncClient
        if (
            current is not None
            and not getattr(current, "is_closed", False)
            and self._keys[kind] == key
            and type(current) is maker
        ):
            return current
        kwargs = client_kwargs(settings, use_proxy=True)
        kwargs["follow_redirects"] = follow
        client = httpx.AsyncClient(**kwargs)
        self._drop(current)
        self._clients[kind] = client
        self._keys[kind] = key
        return client

    def site(self, settings: Settings) -> httpx.AsyncClient:
        return self._open("site", settings, True)

    def image(self, settings: Settings) -> httpx.AsyncClient:
        return self._open("image", settings, False)

    def _drop(self, client: httpx.AsyncClient | None) -> None:
        if client is None:
            return
        close = getattr(client, "aclose", None)
        if close is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(close())

    async def aclose(self) -> None:
        for kind, client in list(self._clients.items()):
            self._clients[kind] = None
            self._keys[kind] = None
            if client is not None and hasattr(client, "aclose"):
                await client.aclose()


pool = ClientPool()


def site_client(settings: Settings) -> _Held:
    return _Held(pool.site(settings))


def image_client(settings: Settings) -> httpx.AsyncClient:
    return pool.image(settings)


async def close_clients() -> None:
    await pool.aclose()


def direct_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(**client_kwargs(settings, use_proxy=False))

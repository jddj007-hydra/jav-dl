from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
log = logging.getLogger("app.http")

# Proxy CONNECT + origin TLS is not covered by httpcore's connect retries.
# One extra attempt on a fresh tunnel recovers most empty ConnectError flakes.
# Timeouts are not retried: a second wait holds a pool slot and the next list
# request then sits until http_timeout.
TRANSIENT_HTTP = (httpx.ConnectError, httpx.RemoteProtocolError)
DEAD_HTTP = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)
CONNECT_TIMEOUT = 8.0
POOL_TIMEOUT = 5.0
PROXY_KEEPALIVE = 8
PROXY_MAX_CONNECTIONS = 16
PROXY_KEEPALIVE_EXPIRY = 4.0


class RetryTransport(httpx.AsyncBaseTransport):
    """Retry the whole request once when the proxy tunnel or TLS handshake dies."""

    def __init__(self, inner: httpx.AsyncBaseTransport, retries: int = 1):
        self._inner = inner
        self._retries = retries

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last: BaseException | None = None
        for attempt in range(self._retries + 1):
            try:
                return await self._inner.handle_async_request(request)
            except TRANSIENT_HTTP as exc:
                last = exc
                if attempt >= self._retries:
                    raise
                log.info(
                    "HTTP 重试 %s %s: %s",
                    request.method,
                    request.url.host,
                    describe_http_error(exc),
                )
        raise last or RuntimeError("HTTP 重试失败")

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


def _looks_like_deadline(text: str) -> bool:
    low = text.lower()
    return "deadline exceeded" in low or "cancelled via cancel scope" in low


def describe_http_error(exc: BaseException) -> str:
    """httpx timeouts and proxy ConnectError often stringify to empty."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text:
            return "连接超时" if _looks_like_deadline(text) else text
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.TimeoutException):
        return "连接超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "连接被断开"
    return type(exc).__name__


def proxy_url(settings: Settings, *, use_proxy: bool) -> str | None:
    if use_proxy and settings.proxy_enabled and settings.proxy_url:
        return settings.proxy_url
    return None


def client_timeout(settings: Settings) -> httpx.Timeout:
    total = float(settings.http_timeout)
    return httpx.Timeout(
        total,
        connect=min(CONNECT_TIMEOUT, total),
        pool=min(POOL_TIMEOUT, total),
    )


def client_limits(*, proxy: str | None) -> httpx.Limits:
    if proxy:
        # Short expiry drops half-closed CONNECT tunnels; a few keepalive
        # sockets let a list of covers reuse one tunnel instead of 24 new TLS
        # handshakes. Cap concurrency so sing-box is not flooded.
        return httpx.Limits(
            max_keepalive_connections=PROXY_KEEPALIVE,
            max_connections=PROXY_MAX_CONNECTIONS,
            keepalive_expiry=PROXY_KEEPALIVE_EXPIRY,
        )
    return httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=5.0)


def client_kwargs(settings: Settings, *, use_proxy: bool) -> dict:
    proxy = proxy_url(settings, use_proxy=use_proxy)
    limits = client_limits(proxy=proxy)
    transport = RetryTransport(
        httpx.AsyncHTTPTransport(proxy=proxy, verify=settings.verify_tls, limits=limits)
    )
    return {
        "follow_redirects": True,
        "timeout": client_timeout(settings),
        "headers": {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        "limits": limits,
        "transport": transport,
    }


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
        proxy = proxy_url(settings, use_proxy=True) or ""
        limits = client_limits(proxy=proxy or None)
        return (
            proxy,
            bool(settings.verify_tls),
            float(settings.http_timeout),
            follow,
            limits.max_keepalive_connections,
            limits.max_connections,
            limits.keepalive_expiry,
        )

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
        self._guard(kind, client)
        self._drop(current)
        self._clients[kind] = client
        self._keys[kind] = key
        return client

    def site(self, settings: Settings) -> httpx.AsyncClient:
        return self._open("site", settings, True)

    def image(self, settings: Settings) -> httpx.AsyncClient:
        return self._open("image", settings, False)

    def invalidate(self, kind: str, client: httpx.AsyncClient | None = None) -> None:
        current = self._clients.get(kind)
        if current is None:
            return
        if client is not None and current is not client:
            return
        self._clients[kind] = None
        self._keys[kind] = None
        log.warning("HTTP 丢弃 %s 客户端: 代理隧道失效", kind)
        self._drop(current)

    def _guard(self, kind: str, client: httpx.AsyncClient) -> None:
        bound = getattr(client, "send", None)
        if bound is None:
            return

        async def send(request, *args, **kwargs):
            try:
                return await bound(request, *args, **kwargs)
            except DEAD_HTTP:
                self.invalidate(kind, client)
                raise

        client.send = send  # type: ignore[method-assign]

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

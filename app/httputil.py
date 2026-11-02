from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager

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
DEAD_HTTP = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
)
CONNECT_TIMEOUT = 8.0
POOL_TIMEOUT = 5.0
PROXY_KEEPALIVE = 8
PROXY_MAX_CONNECTIONS = 16
PROXY_KEEPALIVE_EXPIRY = 4.0
_pool_retry = contextvars.ContextVar("javdl_http_pool_retry", default=False)


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


def _looks_like_tls_eof(text: str) -> bool:
    low = text.lower()
    return (
        "unexpected_eof" in low
        or "eof occurred in violation of protocol" in low
        or "connection reset" in low
    )


def _client_closed(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and "has been closed" in str(exc).lower()


def _is_dead_http(exc: BaseException) -> bool:
    return isinstance(exc, DEAD_HTTP) or _client_closed(exc)


def describe_http_error(exc: BaseException) -> str:
    """httpx timeouts and proxy ConnectError often stringify to empty."""
    if _client_closed(exc):
        return "连接被断开"
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text:
            if _looks_like_deadline(text):
                return "连接超时"
            if _looks_like_tls_eof(text):
                return "连接被断开"
            return text
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.TimeoutException):
        return "连接超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败"
    if isinstance(exc, httpx.RemoteProtocolError) or isinstance(exc, httpx.ReadError):
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


class _Live:
    """Always talk to the current pooled client so a dropped tunnel does not 500."""

    def __init__(self, kind: str, settings: Settings):
        self._kind = kind
        self._settings = settings

    def _live(self) -> httpx.AsyncClient:
        if self._kind == "image":
            return pool.image(self._settings)
        return pool.site(self._settings)

    def __getattr__(self, name: str):
        return getattr(self._live(), name)


class _Held:
    """Context manager that borrows a shared client and leaves it open."""

    def __init__(self, settings: Settings, kind: str = "site"):
        self._settings = settings
        self._kind = kind

    async def __aenter__(self) -> _Live:
        return _Live(self._kind, self._settings)

    async def __aexit__(self, *exc) -> bool:
        return False


class ClientPool:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient | None] = {"site": None, "image": None}
        self._keys: dict[str, tuple | None] = {"site": None, "image": None}
        self._settings: dict[str, Settings | None] = {"site": None, "image": None}
        self._follow: dict[str, bool] = {"site": True, "image": False}
        self._inflight: dict[int, int] = {}
        self._retired: set[int] = set()

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
        self._clients[kind] = client
        self._keys[kind] = key
        self._settings[kind] = settings
        self._follow[kind] = follow
        if current is not None and current is not client:
            self._retire(current)
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
        self._retire(current)

    def _guard(self, kind: str, client: httpx.AsyncClient) -> None:
        bound = getattr(client, "send", None)
        if bound is None:
            return

        async def send(request, *args, **kwargs):
            cid = id(client)
            self._inflight[cid] = self._inflight.get(cid, 0) + 1
            try:
                try:
                    return await bound(request, *args, **kwargs)
                except Exception as exc:
                    if not _is_dead_http(exc):
                        raise
                    self.invalidate(kind, client)
                    if _pool_retry.get():
                        raise
                    settings = self._settings.get(kind)
                    if settings is None:
                        raise
                    token = _pool_retry.set(True)
                    try:
                        fresh = self._open(kind, settings, self._follow[kind])
                        if fresh is client:
                            raise
                        log.info(
                            "HTTP 换新客户端重试 %s %s",
                            request.method,
                            request.url.host,
                        )
                        return await fresh.send(request, *args, **kwargs)
                    finally:
                        _pool_retry.reset(token)
            finally:
                left = self._inflight.get(cid, 1) - 1
                if left <= 0:
                    self._inflight.pop(cid, None)
                else:
                    self._inflight[cid] = left
                self._maybe_close(client)

        client.send = send  # type: ignore[method-assign]

    def _retire(self, client: httpx.AsyncClient | None) -> None:
        if client is None:
            return
        self._retired.add(id(client))
        self._maybe_close(client)

    def _maybe_close(self, client: httpx.AsyncClient | None) -> None:
        if client is None:
            return
        cid = id(client)
        if cid not in self._retired:
            return
        if self._inflight.get(cid, 0) > 0:
            return
        if any(current is client for current in self._clients.values()):
            return
        self._retired.discard(cid)
        self._drop(client)

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
        pending: list[httpx.AsyncClient] = []
        for kind, client in list(self._clients.items()):
            self._clients[kind] = None
            self._keys[kind] = None
            self._settings[kind] = None
            if client is not None:
                pending.append(client)
        self._retired.clear()
        self._inflight.clear()
        for client in pending:
            if hasattr(client, "aclose"):
                await client.aclose()


pool = ClientPool()


def site_client(settings: Settings) -> _Held:
    return _Held(settings)


def image_client(settings: Settings) -> _Live:
    return _Live("image", settings)


async def close_clients() -> None:
    await pool.aclose()


def direct_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(**client_kwargs(settings, use_proxy=False))


@asynccontextmanager
async def direct_site(settings: Settings):
    """Throwaway client that skips the HTTP proxy. 磁力猫 often SSL-EOFs through it."""
    client = httpx.AsyncClient(**client_kwargs(settings, use_proxy=False))
    try:
        yield client
    finally:
        await client.aclose()

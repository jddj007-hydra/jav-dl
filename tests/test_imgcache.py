import asyncio
import os

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.httputil import (
    RetryTransport,
    client_kwargs,
    client_timeout,
    close_clients,
    describe_http_error,
    pool,
    site_client,
)
from app.routers import health as health_mod
from app.imgcache import trim_img_cache
from app.routers.images import fetch_image


def test_trim_img_cache_drops_the_oldest_group(tmp_path):
    old = tmp_path / "old"
    old.write_bytes(b"a" * 30)
    note = tmp_path / "old.ct"
    note.write_text("image/jpeg", encoding="utf-8")
    new = tmp_path / "new"
    new.write_bytes(b"b" * 30)
    os.utime(old, (1, 1))
    os.utime(note, (1, 1))
    os.utime(new, (100, 100))
    trim_img_cache(tmp_path, limit=40)
    assert not old.exists()
    assert not note.exists()
    assert new.exists()


def test_site_client_is_reused_and_left_open():
    settings = Settings(proxy_enabled=False, verify_tls=False, http_timeout=5)

    async def run():
        async with site_client(settings) as first:
            shared = pool.site(settings)
            async with site_client(settings) as second:
                assert pool.site(settings) is shared
                assert first.is_closed is False
                assert second.is_closed is False
            assert shared.is_closed is False
        await close_clients()

    asyncio.run(run())


def test_site_client_rebuilds_when_proxy_changes():
    settings = Settings(proxy_enabled=False, verify_tls=False, http_timeout=5)

    async def run():
        async with site_client(settings) as first:
            original = pool.site(settings)
            updated = settings.model_copy(update={"proxy_enabled": True, "proxy_url": "http://127.0.0.1:7890"})
            async with site_client(updated) as second:
                rebuilt = pool.site(updated)
                assert rebuilt is not original
                assert first.is_closed is False
                assert second.is_closed is False
        await close_clients()

    asyncio.run(run())


def test_proxy_client_keeps_short_keepalive_and_retries():
    settings = Settings(proxy_enabled=True, proxy_url="http://127.0.0.1:7890", http_timeout=5)
    kwargs = client_kwargs(settings, use_proxy=True)
    assert kwargs["limits"].max_keepalive_connections == 8
    assert kwargs["limits"].max_connections == 16
    assert kwargs["limits"].keepalive_expiry == 4.0
    assert isinstance(kwargs["transport"], RetryTransport)
    assert kwargs["timeout"].connect == 5.0
    direct = client_kwargs(settings, use_proxy=False)
    assert "proxy" not in direct
    assert direct["limits"].max_keepalive_connections == 20


def test_retry_transport_retries_connect_error_once():
    class BoomThenOk(httpx.AsyncBaseTransport):
        def __init__(self):
            self.calls = 0

        async def handle_async_request(self, request):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("")
            return httpx.Response(200, request=request, content=b"ok")

    inner = BoomThenOk()

    async def run():
        response = await RetryTransport(inner).handle_async_request(
            httpx.Request("GET", "https://www.javbus.com/")
        )
        assert response.status_code == 200
        assert inner.calls == 2

    asyncio.run(run())


def test_retry_transport_gives_up_after_one_retry():
    class AlwaysDown(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("")

    async def run():
        with pytest.raises(httpx.ConnectError):
            await RetryTransport(AlwaysDown()).handle_async_request(
                httpx.Request("GET", "https://clm64.top/")
            )

    asyncio.run(run())


def test_retry_transport_does_not_retry_timeout():
    class Once(httpx.AsyncBaseTransport):
        def __init__(self):
            self.calls = 0

        async def handle_async_request(self, request):
            self.calls += 1
            raise httpx.ConnectTimeout("Cancelled via cancel scope x; reason: deadline exceeded")

    inner = Once()

    async def run():
        with pytest.raises(httpx.ConnectTimeout):
            await RetryTransport(inner).handle_async_request(
                httpx.Request("GET", "https://www.javbus.com/")
            )
        assert inner.calls == 1

    asyncio.run(run())


def test_describe_http_error_timeout_without_message():
    assert describe_http_error(httpx.ConnectTimeout("")) == "连接超时"
    assert describe_http_error(httpx.ReadTimeout("timed out")) == "timed out"
    assert describe_http_error(httpx.ConnectError("")) == "连接失败"
    assert (
        describe_http_error(
            httpx.ReadTimeout("Cancelled via cancel scope 77d16028c910; reason: deadline exceeded")
        )
        == "连接超时"
    )
    assert (
        describe_http_error(RuntimeError("Cannot send a request, as the client has been closed."))
        == "连接被断开"
    )


def test_client_timeout_caps_pool_wait():
    timeout = client_timeout(Settings(http_timeout=20))
    assert timeout.connect == 8.0
    assert timeout.pool == 5.0


def test_dead_tunnel_retries_on_fresh_client_and_held_client_follows(monkeypatch):
    calls = {"n": 0}

    class Flaky(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("ssl eof")
            return httpx.Response(200, request=request, content=b"ok")

    real_kwargs = client_kwargs

    def fake_kwargs(settings, *, use_proxy):
        kwargs = real_kwargs(settings, use_proxy=use_proxy)
        kwargs["transport"] = Flaky()
        return kwargs

    monkeypatch.setattr("app.httputil.client_kwargs", fake_kwargs)

    async def run():
        await close_clients()
        settings = Settings(proxy_enabled=False, verify_tls=False, http_timeout=5)
        async with site_client(settings) as client:
            first = await client.get("https://clm64.top/")
            second = await client.get("https://clm.cc/")
            assert first.status_code == 200
            assert second.status_code == 200
        await close_clients()

    asyncio.run(run())
    assert calls["n"] >= 3


def test_timeout_invalidates_shared_client(monkeypatch):
    class Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ReadTimeout("Cancelled via cancel scope x; reason: deadline exceeded")

    real_kwargs = client_kwargs

    def fake_kwargs(settings, *, use_proxy):
        kwargs = real_kwargs(settings, use_proxy=use_proxy)
        kwargs["transport"] = Boom()
        return kwargs

    monkeypatch.setattr("app.httputil.client_kwargs", fake_kwargs)

    async def run():
        await close_clients()
        settings = Settings(proxy_enabled=False, verify_tls=False, http_timeout=5)
        first = pool.site(settings)
        with pytest.raises(httpx.ReadTimeout):
            await first.get("https://www.javbus.com/")
        second = pool.site(settings)
        assert first is not second
        await close_clients()

    asyncio.run(run())


def test_health_probe_does_not_use_shared_pool(monkeypatch):
    used = {"own": 0}

    class FakeClient:
        def __init__(self, **kwargs):
            used["own"] += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, timeout=None):
            return httpx.Response(200, request=httpx.Request("GET", url))

    def boom(*args, **kwargs):
        raise AssertionError("health probe used the shared pool")

    monkeypatch.setattr(health_mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(pool, "site", boom)

    async def run():
        ok = await health_mod._site_ok(Settings(proxy_enabled=False), "https://www.javbus.com/")
        assert ok is True
        assert used["own"] == 1

    asyncio.run(run())


def test_image_connect_error_is_502(monkeypatch):
    class FakeClient:
        async def get(self, url, headers=None):
            raise httpx.ConnectError("")

    monkeypatch.setattr("app.routers.images.image_client", lambda settings: FakeClient())

    async def run():
        with pytest.raises(HTTPException) as caught:
            await fetch_image(
                Settings(),
                "https://www.javbus.com/a.jpg",
                "https://www.javbus.com/",
                "https://www.javbus.com",
            )
        assert caught.value.status_code == 502
        assert "连接失败" in caught.value.detail

    asyncio.run(run())

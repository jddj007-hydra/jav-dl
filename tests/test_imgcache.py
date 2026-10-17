import asyncio
import os

import httpx

from app.config import Settings
from app.httputil import (
    CLIENT_MAX_AGE,
    client_kwargs,
    close_clients,
    describe_http_error,
    pool,
    site_client,
)
from app.imgcache import trim_img_cache


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
            async with site_client(settings) as second:
                assert first is second
            assert first.is_closed is False
        await close_clients()

    asyncio.run(run())


def test_site_client_recycles_after_max_age():
    settings = Settings(proxy_enabled=False, verify_tls=False, http_timeout=5)

    async def run():
        async with site_client(settings) as first:
            pool._born["site"] = pool._born["site"] - CLIENT_MAX_AGE - 1
            async with site_client(settings) as second:
                assert first is not second
        await close_clients()

    asyncio.run(run())


def test_proxy_client_kwargs_disable_keepalive():
    settings = Settings(proxy_enabled=True, proxy_url="http://127.0.0.1:7890", http_timeout=5)
    kwargs = client_kwargs(settings, use_proxy=True)
    assert kwargs["limits"].max_keepalive_connections == 0
    direct = client_kwargs(settings, use_proxy=False)
    assert "proxy" not in direct
    assert "limits" not in direct


def test_describe_http_error_timeout_without_message():
    assert describe_http_error(httpx.ConnectTimeout("")) == "连接超时"
    assert describe_http_error(httpx.ReadTimeout("timed out")) == "timed out"
    assert describe_http_error(httpx.ConnectError("")) == "ConnectError"

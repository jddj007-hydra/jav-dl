import asyncio
import os

from app.config import Settings
from app.httputil import close_clients, site_client
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

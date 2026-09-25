import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.config import Settings
from app.main import BasicAuthMiddleware, warn_open_auth
from app.routers.images import _allowed, fetch_image, image_media_type


def test_image_host_must_be_the_domain_itself():
    base = "https://www.javbus.com"
    assert _allowed("https://www.javbus.com/pics/a.jpg", base)
    assert _allowed("https://pics.dmm.co.jp/x.jpg", base)
    assert _allowed("https://cdn.theporndb.net/p.jpg", base)
    assert _allowed("https://pics.seejav.bid/a.jpg", base)
    assert _allowed("https://cdnbus.org/a.jpg", base)
    assert _allowed("https://mirror.example/a.jpg", "https://mirror.example")
    assert not _allowed("https://javbus.com.evil.com/a.jpg", base)
    assert not _allowed("https://notjavbus.com/a.jpg", base)
    assert not _allowed("https://seejav.evil.com/a.jpg", base)
    assert not _allowed("https://evilseejav.attacker.com/a.jpg", base)
    assert not _allowed("https://nottheporndb.net.evil.com/p.jpg", base)
    assert not _allowed("http://127.0.0.1/secret", base)
    assert not _allowed("https://user:pass@www.javbus.com/a.jpg", base)


def test_image_type_comes_from_bytes_not_the_header():
    assert image_media_type(b"\xff\xd8\xff rest") == "image/jpeg"
    assert image_media_type(b"\x89PNG\r\n\x1a\n rest") == "image/png"
    assert image_media_type(b"<html>not an image</html>") is None


def test_redirect_off_the_allowlist_is_rejected(monkeypatch):
    seen = {}

    class FakeResponse:
        def __init__(self, status_code, content=b"", headers=None):
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["follow"] = kwargs.get("follow_redirects")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            if "javbus.com" in url:
                return FakeResponse(302, headers={"location": "http://127.0.0.1/secret"})
            return FakeResponse(200, b"\xff\xd8\xffok")

    monkeypatch.setattr("app.httputil.httpx.AsyncClient", FakeClient)

    async def run():
        with pytest.raises(HTTPException) as caught:
            await fetch_image(Settings(), "https://www.javbus.com/a.jpg", "https://www.javbus.com/", "https://www.javbus.com")
        assert caught.value.status_code == 400

    asyncio.run(run())
    assert seen["follow"] is False


def test_html_body_is_not_returned_as_an_image(monkeypatch):
    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.content = b"<html>nope</html>"
            self.headers = {"content-type": "text/html"}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr("app.httputil.httpx.AsyncClient", FakeClient)

    async def run():
        with pytest.raises(HTTPException) as caught:
            await fetch_image(Settings(), "https://www.javbus.com/a.jpg", "https://www.javbus.com/", "https://www.javbus.com")
        assert caught.value.status_code == 502

    asyncio.run(run())


def test_health_requires_login_when_auth_is_on():
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.state.settings = Settings(auth_user="u", auth_pass="p")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/health")
            assert denied.status_code == 401
            allowed = await client.get("/api/health", auth=("u", "p"))
            assert allowed.status_code == 200

    asyncio.run(run())


def test_open_auth_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="app")
    warn_open_auth(Settings(auth_user="", auth_pass=""))
    warn_open_auth(Settings(auth_user="u", auth_pass="p"))
    messages = [rec.message for rec in caplog.records]
    assert len(messages) == 1
    assert "AUTH_USER" in messages[0]


def test_settings_show_verify_tls_without_changing_the_default():
    assert Settings.model_fields["verify_tls"].default is False
    assert Settings(verify_tls=False).public_dict()["verify_tls"] is False
    assert Settings(verify_tls=True).public_dict()["verify_tls"] is True

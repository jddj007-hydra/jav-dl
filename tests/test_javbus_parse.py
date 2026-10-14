import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.sources.javbus import (
    MetadataError,
    detect_blocker,
    fetch_javbus_html,
    fetch_latest,
    fetch_metadata,
    parse_javbus,
    search_works,
)

VERIFY = "JavBus 返回了验证页，不是没有这部片。换代理或稍后再试"
NORMAL_DETAIL = """
<html><body>
<h3>IPX-001 测试标题</h3>
<a class="bigImage" href="/pics/cover/x.jpg"><img src="/pics/cover/x.jpg"></a>
<div class="col-md-3 info">
  <p><span class="header">製作商:</span> <a href="/studio/1">Studio A</a></p>
</div>
</body></html>
"""

HTML = (Path(__file__).parent / "fixtures" / "javbus_ssis001.html").read_text(
    encoding="utf-8"
)


def test_parse_ssis001():
    meta = parse_javbus(HTML, "https://www.javbus.com", "SSIS-001")
    assert meta["code"] == "SSIS-001"
    assert "禁欲" in meta["title"] or "禁慾" in meta["title"]
    names = [a["name"] if isinstance(a, dict) else a for a in meta["actors"]]
    assert "葵つかさ" in names
    assert meta["actors"][0]["photo"]
    assert "/star/" in meta["actors"][0]["url"]
    assert "/studio/" in meta["studio_url"]
    assert "/label/" in meta["label_url"]
    assert meta["release_date"] == "2021-02-18"
    assert meta["cover"]
    assert "javbus.com" in meta["cover"]
    assert meta["studio"]
    assert meta["label"] == "S1 NO.1 STYLE"
    assert len(meta["samples"]) >= 6
    assert meta["samples"][0]["thumb"]
    genres = {item["name"]: item["url"] for item in meta["genres"]}
    assert genres["多P"].endswith("/genre/3")
    assert genres["DMM獨家"].endswith("/genre/g")


def test_parse_search_boxes():
    from app.sources.javbus import parse_search
    html = """
    <html><body>
    <a class="movie-box" href="https://www.javbus.com/SSIS-001_2021-02-18">
      <div class="photo-frame"><img src="/pics/thumb/x.jpg" title="SSIS-001 禁欲生活"></div>
      <div class="photo-info"><span>SSIS-001 禁欲生活<br>
        <date>SSIS-001</date> / <date>2021-02-18</date>
      </span></div>
    </a>
    <a class="movie-box" href="/MTNDV-1396_2022-04-22">
      <div class="photo-frame"><img src="/pics/thumb/y.jpg" title="葵つかさ 测试"></div>
      <div class="photo-info"><span>葵つかさ 测试<br>
        <date>MTNDV-1396</date> / <date>2022-04-22</date>
      </span></div>
    </a>
    </body></html>
    """
    items = parse_search(html, "https://www.javbus.com")
    assert items[0]["code"] == "SSIS-001"
    assert items[0]["release_date"] == "2021-02-18"
    assert "禁欲" in items[0]["title"]
    assert items[0]["cover"].endswith("/pics/cover/x_b.jpg")
    assert items[1]["cover"].endswith("/pics/cover/y_b.jpg")
    assert items[1]["code"] == "MTNDV-1396"


def test_parse_series_field():
    html = """
    <html><body>
    <h3>IPX-001 测试标题</h3>
    <a class="bigImage" href="/pics/cover/x.jpg"><img src="/pics/cover/x.jpg"></a>
    <div class="col-md-3 info">
      <p><span class="header">製作商:</span> <a href="/studio/1">Studio A</a></p>
      <p><span class="header">系列:</span> <a href="/series/9">某系列</a></p>
    </div>
    </body></html>
    """
    meta = parse_javbus(html, "https://www.javbus.com", "IPX-001")
    assert meta["series"] == "某系列"
    assert meta["series_url"] == "https://www.javbus.com/series/9"
    assert meta["studio"] == "Studio A"
    assert meta["studio_url"] == "https://www.javbus.com/studio/1"


def test_driver_verify_page_is_a_blocker():
    html = """
    <html><body>
      <form action="/driver-verify" method="post"><p>请完成验证</p></form>
    </body></html>
    """
    assert detect_blocker("", html)
    assert detect_blocker("https://www.javbus.com/driver-verify?from=/", "<html><h3>SSIS-001 标题</h3></html>")


def test_cloudflare_page_is_a_blocker():
    html = """
    <html><head><title>Just a moment...</title></head>
    <body>
      <div class="cf-browser-verification">
        <div id="challenge-platform">Enable JavaScript and cookies to continue</div>
      </div>
    </body></html>
    """
    assert detect_blocker("https://www.javbus.com/SSIS-001", html)
    assert detect_blocker("", "<html>challenge-platform</html>")
    assert detect_blocker("", "<html>cf-browser-verification</html>")
    assert detect_blocker("", "<html>Enable JavaScript and cookies</html>")


def test_normal_detail_page_is_not_a_blocker():
    assert not detect_blocker("https://www.javbus.com/IPX-001", NORMAL_DETAIL)
    meta = parse_javbus(NORMAL_DETAIL, "https://www.javbus.com", "IPX-001")
    assert meta["code"] == "IPX-001"
    assert meta["title"] == "测试标题"
    assert meta["studio"] == "Studio A"


def _patch_site(monkeypatch, handler):
    class _Client:
        async def get(self, url, *args, **kwargs):
            return handler(url)

    class _Hold:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.sources.javbus.site_client", lambda settings: _Hold())


def _response(url: str, status: int, text: str) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def test_fetch_metadata_stops_on_verify_page(monkeypatch):
    calls: list[str] = []

    def handler(url: str) -> httpx.Response:
        calls.append(url)
        return _response(url, 200, "<html><body><form action='/driver-verify'></form></body></html>")

    _patch_site(monkeypatch, handler)

    with pytest.raises(MetadataError) as exc:
        asyncio.run(fetch_metadata(Settings(), "SSIS-001"))
    assert str(exc.value) == VERIFY
    assert exc.value.status == 503
    assert calls == ["https://www.javbus.com/SSIS-001"]


def test_fetch_metadata_challenge_status_is_verify_page(monkeypatch):
    calls: list[str] = []

    def handler(url: str) -> httpx.Response:
        calls.append(url)
        return _response(url, 403, "<html><title>Just a moment...</title><div class='challenge-platform'></div></html>")

    _patch_site(monkeypatch, handler)

    with pytest.raises(MetadataError) as exc:
        asyncio.run(fetch_metadata(Settings(), "SSIS-001"))
    assert str(exc.value) == VERIFY
    assert exc.value.status == 503
    assert "HTTP 403" not in str(exc.value)
    assert calls == ["https://www.javbus.com/SSIS-001"]


def test_fetch_metadata_plain_404_still_tries_uncensored(monkeypatch):
    calls: list[str] = []

    def handler(url: str) -> httpx.Response:
        calls.append(url)
        return _response(url, 404, "<html><body><h1>404</h1></body></html>")

    _patch_site(monkeypatch, handler)

    with pytest.raises(MetadataError) as exc:
        asyncio.run(fetch_metadata(Settings(), "SSIS-001"))
    assert exc.value.status == 404
    assert "未找到" in str(exc.value)
    assert calls == [
        "https://www.javbus.com/SSIS-001",
        "https://www.javbus.com/uncensored/SSIS-001",
    ]


def test_fetch_metadata_normal_detail_skips_uncensored(monkeypatch):
    calls: list[str] = []

    def handler(url: str) -> httpx.Response:
        calls.append(url)
        return _response(url, 200, NORMAL_DETAIL)

    _patch_site(monkeypatch, handler)
    meta = asyncio.run(fetch_metadata(Settings(), "IPX-001"))
    assert meta["title"] == "测试标题"
    assert calls == ["https://www.javbus.com/IPX-001"]


def test_other_fetches_reject_verify_pages_and_keep_plain_404(monkeypatch):
    def challenge(url: str) -> httpx.Response:
        return _response(url, 503, "<html>cf-browser-verification Enable JavaScript and cookies</html>")

    _patch_site(monkeypatch, challenge)
    with pytest.raises(MetadataError) as html_exc:
        asyncio.run(fetch_javbus_html(Settings(), "https://www.javbus.com/star/2xi"))
    assert str(html_exc.value) == VERIFY
    assert html_exc.value.status == 503

    with pytest.raises(MetadataError) as latest_exc:
        asyncio.run(fetch_latest(Settings(), "censored", 1))
    assert latest_exc.value.status == 503

    with pytest.raises(MetadataError) as search_exc:
        asyncio.run(search_works(Settings(), "葵"))
    assert search_exc.value.status == 503

    def missing(url: str) -> httpx.Response:
        return _response(url, 404, "<html><body>not found</body></html>")

    _patch_site(monkeypatch, missing)
    assert asyncio.run(fetch_latest(Settings(), "censored", 1)) == []
    with pytest.raises(MetadataError) as gone:
        asyncio.run(search_works(Settings(), "没有这部"))
    assert gone.value.status == 404
    with pytest.raises(MetadataError) as page:
        asyncio.run(fetch_javbus_html(Settings(), "https://www.javbus.com/star/missing"))
    assert page.value.status == 404
    assert str(page.value) == "JavBus HTTP 404"

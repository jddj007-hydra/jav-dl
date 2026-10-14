import asyncio
import json
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.config import Settings
from app.db import Database
from app.nfo import build_nfo
from app.routers.images import _allowed, image_referer
from app.scrape import ScrapeError, fetch_cover_bytes, resolve_metadata
from app.sources.javbus import CACHE_VER, MetadataError
from app.sources.javdb import fetch_javdb_metadata, parse_javdb_detail, parse_javdb_search

BASE = "https://javdb.com"
DETAIL_URL = f"{BASE}/v/right"
META_KEYS = {
    "code", "title", "cover", "actors", "studio", "studio_url", "label", "label_url",
    "series", "series_url", "director", "release_date", "runtime", "genres", "samples",
    "source", "url",
}

SEARCH_HIT = """
<div class="movie-list">
  <a class="box" href="/v/SSIS-001" title="ABC-123 别的">
    <div class="video-title"><strong>ABC-123</strong> 别的</div>
  </a>
  <a class="box" href="https://evil.example/steal" title="SSIS-001 外站">
    <div class="video-title"><strong>SSIS-001</strong> 外站</div>
  </a>
  <a class="box" href="/v/near">
    <div class="video-title"><strong>SSIS-002</strong> 接近</div>
  </a>
  <a class="box" href="/v/right">
    <div class="video-title"><strong>SSIS-001</strong> 禁欲之后</div>
  </a>
</div>
"""

SEARCH_MISS = """
<a class="box" href="/v/SSIS-001">
  <div class="video-title"><strong>ABC-123</strong> 番号写在链接里</div>
</a>
<a class="box" href="/v/other">
  <div class="video-title"><strong>SSIS-002</strong> 另一部</div>
</a>
"""

SEARCH_COMPACT = """
<a class="box" href="/v/compact">
  <div class="video-title"><strong>SSIS001</strong> 紧凑番号</div>
</a>
"""

DETAIL = """
<div class="movie-panel">
  <h2 class="title"><strong class="current-title">SSIS-001</strong> 禁欲之后</h2>
  <img class="video-cover" src="https://c0.jdbstatic.com/covers/ab/ssis.jpg" />
  <div class="panel-block"><strong>日期:</strong><span class="value">2021-02-18</span></div>
  <div class="panel-block"><strong>時長:</strong><span class="value">120 分鐘</span></div>
  <div class="panel-block"><strong>導演:</strong><span class="value"><a href="/directors/9">某某</a></span></div>
  <div class="panel-block"><strong>片商:</strong><span class="value"><a href="/makers/s1">S1</a></span></div>
  <div class="panel-block"><strong>發行:</strong><span class="value"><a href="/publishers/s1">S1 NO.1 STYLE</a></span></div>
  <div class="panel-block"><strong>系列:</strong><span class="value"><a href="/series/9">系列名</a></span></div>
  <div class="panel-block">
    <strong>類別:</strong>
    <span class="value tags">
      <a href="/tags/1">单体作品</a>
      <a href="/tags/2">巨乳</a>
    </span>
  </div>
  <div class="panel-block">
    <strong>演員:</strong>
    <span class="value actors">
      <a href="/actors/2xi"><img src="https://c0.jdbstatic.com/avatars/2xi.jpg" />葵つかさ</a>
    </span>
  </div>
  <div class="preview-images">
    <a href="https://c0.jdbstatic.com/samples/1.jpg"><img src="https://c0.jdbstatic.com/samples/1s.jpg" /></a>
  </div>
</div>
"""

DETAIL_BY_LABEL = """
<div class="movie-panel">
  <h2 class="title">SSIS-001 禁欲之后</h2>
  <div class="panel-block"><strong>演員:</strong><span class="value"><a href="/actors/2xi">葵つかさ</a></span></div>
  <div class="panel-block"><strong>類別:</strong><span class="value"><a href="/tags/2">巨乳</a></span></div>
</div>
"""


def test_parse_javdb_search_requires_the_same_code():
    assert parse_javdb_search(SEARCH_MISS, BASE, "SSIS-001") == ""
    assert parse_javdb_search(SEARCH_HIT, BASE, "SSIS-001") == DETAIL_URL
    assert parse_javdb_search(SEARCH_COMPACT, BASE, "SSIS-001") == f"{BASE}/v/compact"
    assert parse_javdb_search(SEARCH_HIT, BASE, "SSIS-002") == f"{BASE}/v/near"


def test_parse_javdb_detail_matches_javbus_shape():
    meta = parse_javdb_detail(DETAIL, BASE, "SSIS-001", page_url=DETAIL_URL)
    assert set(meta) == META_KEYS
    assert meta["code"] == "SSIS-001"
    assert meta["title"] == "禁欲之后"
    assert meta["cover"] == "https://c0.jdbstatic.com/covers/ab/ssis.jpg"
    assert meta["release_date"] == "2021-02-18"
    assert meta["runtime"] == "120 分鐘"
    assert meta["director"] == "某某"
    assert meta["studio"] == "S1"
    assert meta["studio_url"] == f"{BASE}/makers/s1"
    assert meta["label"] == "S1 NO.1 STYLE"
    assert meta["label_url"] == f"{BASE}/publishers/s1"
    assert meta["series"] == "系列名"
    assert meta["series_url"] == f"{BASE}/series/9"
    assert meta["genres"] == [
        {"name": "单体作品", "url": f"{BASE}/tags/1"},
        {"name": "巨乳", "url": f"{BASE}/tags/2"},
    ]
    assert meta["actors"] == [{
        "name": "葵つかさ",
        "photo": "https://c0.jdbstatic.com/avatars/2xi.jpg",
        "url": f"{BASE}/actors/2xi",
    }]
    assert meta["samples"] == [{
        "thumb": "https://c0.jdbstatic.com/samples/1s.jpg",
        "full": "https://c0.jdbstatic.com/samples/1.jpg",
    }]
    assert meta["source"] == "javdb"
    assert meta["url"] == DETAIL_URL

    labeled = parse_javdb_detail(DETAIL_BY_LABEL, BASE, "SSIS-001")
    assert labeled["actors"][0]["name"] == "葵つかさ"
    assert labeled["genres"] == [{"name": "巨乳", "url": f"{BASE}/tags/2"}]


def test_javdb_nfo_uniqueid_is_not_marked_javbus():
    javdb = ET.fromstring(build_nfo({
        "code": "SSIS-001",
        "title": "禁欲之后",
        "source": "javdb",
    }))
    kinds = [item.attrib.get("type") for item in javdb.findall("uniqueid")]
    assert kinds == ["num", "javdb"]
    assert javdb.find("uniqueid[@type='javdb']").text == "SSIS-001"

    bus = ET.fromstring(build_nfo({
        "code": "SSIS-001",
        "title": "禁欲之后",
        "source": "javbus",
    }))
    assert [item.attrib.get("type") for item in bus.findall("uniqueid")] == ["num", "javbus"]
    plain = ET.fromstring(build_nfo({"code": "SSIS-001", "title": "禁欲之后"}))
    assert [item.attrib.get("type") for item in plain.findall("uniqueid")] == ["num", "javbus"]


def _response(url: str, status: int, text: str) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def _patch_javdb(monkeypatch, handler):
    class _Hold:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, **kwargs):
            return handler(url, headers or {})

    monkeypatch.setattr("app.sources.javdb.site_client", lambda settings: _Hold())


def test_fetch_javdb_sends_cookie_and_skips_a_blank_one(monkeypatch):
    calls = []

    def handler(url, headers):
        calls.append((url, dict(headers)))
        if "/search" in url:
            return _response(url, 200, SEARCH_HIT)
        return _response(url, 200, DETAIL)

    _patch_javdb(monkeypatch, handler)
    meta = asyncio.run(fetch_javdb_metadata(
        Settings(javdb_base=BASE, javdb_cookie="cf_clearance=secret"),
        "SSIS-001",
    ))
    assert meta["source"] == "javdb"
    assert meta["title"] == "禁欲之后"
    assert calls[0][0] == f"{BASE}/search?q=SSIS-001&f=all"
    assert calls[0][1]["Cookie"] == "cf_clearance=secret"
    assert calls[1][0] == DETAIL_URL

    calls.clear()
    asyncio.run(fetch_javdb_metadata(Settings(javdb_base=BASE, javdb_cookie="  "), "SSIS-001"))
    assert "Cookie" not in calls[0][1]


def test_fetch_javdb_miss_and_blocker_do_not_invent_a_hit(monkeypatch):
    calls = []

    def miss(url, headers):
        calls.append(url)
        return _response(url, 200, SEARCH_MISS)

    _patch_javdb(monkeypatch, miss)
    with pytest.raises(MetadataError) as missing:
        asyncio.run(fetch_javdb_metadata(Settings(javdb_base=BASE), "SSIS-001"))
    assert missing.value.status == 404
    assert str(missing.value) == "JavDB 也没有该番号"
    assert calls == [f"{BASE}/search?q=SSIS-001&f=all"]

    def blocked(url, headers):
        calls.append(url)
        return _response(url, 403, "<html><title>Just a moment...</title><div class='challenge-platform'></div></html>")

    calls.clear()
    _patch_javdb(monkeypatch, blocked)
    with pytest.raises(MetadataError) as challenge:
        asyncio.run(fetch_javdb_metadata(Settings(javdb_base=BASE), "SSIS-001"))
    assert challenge.value.status == 503
    assert "JavDB" in str(challenge.value)
    assert "拦截" in str(challenge.value)


class _Mem:
    def __init__(self):
        self.saved = None

    async def get_metadata_any(self, key):
        return None

    async def put_metadata(self, key, payload):
        self.saved = (key, payload)


def _patch_sources(monkeypatch, bus, javdb=None):
    async def fail(settings, code):
        raise AssertionError("不应回退 JavDB")

    monkeypatch.setattr("app.scrape.fetch_metadata", bus)
    monkeypatch.setattr("app.scrape.fetch_javdb_metadata", javdb or fail)


def test_resolve_metadata_falls_back_only_when_javbus_is_404(tmp_path, monkeypatch):
    calls = []

    async def missing(settings, code):
        raise MetadataError("未找到该番号", 404)

    async def found(settings, code):
        calls.append(code)
        return {"code": code, "title": "来自 JavDB", "source": "javdb", "cover": "", "actors": [], "genres": []}

    _patch_sources(monkeypatch, missing, found)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        javdb_base=BASE,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        meta = await resolve_metadata(settings, db, "SSIS-001")
        cached = await db.get_metadata_any(f"{CACHE_VER}:SSIS-001")
        return meta, cached

    meta, cached = asyncio.run(run())
    assert calls == ["SSIS-001"]
    assert meta["source"] == "javdb"
    assert cached["title"] == "来自 JavDB"
    assert cached["source"] == "javdb"

    async def also_missing(settings, code):
        calls.append("javdb")
        raise MetadataError("JavDB 也没有该番号", 404)

    calls.clear()
    _patch_sources(monkeypatch, missing, also_missing)
    db = _Mem()
    with pytest.raises(ScrapeError) as both:
        asyncio.run(resolve_metadata(settings, db, "SSIS-001"))
    assert str(both.value) == "刮削元数据失败: JavBus 和 JavDB 都没有该番号"
    assert db.saved is None

    async def blocked(settings, code):
        calls.append("blocked")
        raise MetadataError("JavDB 被拦截，返回了验证页", 503)

    calls.clear()
    _patch_sources(monkeypatch, missing, blocked)
    db = _Mem()
    with pytest.raises(ScrapeError) as challenge:
        asyncio.run(resolve_metadata(settings, db, "SSIS-001"))
    assert str(challenge.value) == "刮削元数据失败: JavBus 没有该番号；JavDB 被拦截"
    assert db.saved is None


def test_resolve_metadata_does_not_fall_back_for_block_empty_base_fc2_or_network(monkeypatch):
    calls = []

    async def boom(settings, code):
        calls.append(code)
        raise AssertionError("不应回退 JavDB")

    async def blocked(settings, code):
        raise MetadataError("JavBus 返回了验证页，不是没有这部片。换代理或稍后再试", 503)

    settings = Settings(javdb_base=BASE)
    _patch_sources(monkeypatch, blocked, boom)
    with pytest.raises(ScrapeError) as bus_block:
        asyncio.run(resolve_metadata(settings, _Mem(), "SSIS-001"))
    assert calls == []
    assert "JavDB" not in str(bus_block.value)
    assert "验证页" in str(bus_block.value)

    async def missing(settings, code):
        raise MetadataError("未找到该番号", 404)

    _patch_sources(monkeypatch, missing, boom)
    disabled = Settings(javdb_base="")
    with pytest.raises(ScrapeError) as off:
        asyncio.run(resolve_metadata(disabled, _Mem(), "SSIS-001"))
    assert calls == []
    assert "未找到该番号" in str(off.value)

    with pytest.raises(ScrapeError) as fc2:
        asyncio.run(resolve_metadata(settings, _Mem(), "FC2-PPV-1234567"))
    assert calls == []
    assert "未找到该番号" in str(fc2.value)

    async def network(settings, code):
        raise MetadataError("JavBus 请求失败: timed out")

    _patch_sources(monkeypatch, network, boom)
    with pytest.raises(ScrapeError) as net:
        asyncio.run(resolve_metadata(settings, _Mem(), "SSIS-001"))
    assert calls == []
    assert "timed out" in str(net.value)
    assert "JavDB" not in str(net.value)


def test_javdb_cover_host_is_allowed_and_uses_javdb_referer(tmp_path, monkeypatch):
    base = "https://www.javbus.com"
    cover = "https://c0.jdbstatic.com/covers/ab/ssis.jpg"
    assert _allowed(cover, base)
    assert _allowed("https://javdb.com/covers/a.jpg", base)
    assert not _allowed("https://jdbstatic.com.evil.com/a.jpg", base)
    assert not _allowed("https://javdb.com.evil.com/a.jpg", base)
    assert _allowed("https://cdn.mirror.test/a.jpg", base, "https://cdn.mirror.test")

    settings = Settings(javbus_base=base, javdb_base=BASE)
    assert image_referer(settings, cover) == f"{BASE}/"
    assert image_referer(settings, "https://www.javbus.com/pics/a.jpg") == f"{base}/"
    assert image_referer(settings, "https://cdn.theporndb.net/a.jpg") == "https://theporndb.net/"

    seen = {}

    class _Resp:
        status_code = 200
        content = b"\xff\xd8\xff"

    class _Hold:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, **kwargs):
            seen["url"] = url
            seen["headers"] = headers
            return _Resp()

    monkeypatch.setattr("app.scrape.site_client", lambda settings: _Hold())
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        javbus_base=base,
        javdb_base=BASE,
    )
    data = asyncio.run(fetch_cover_bytes(settings, cover))
    assert data.startswith(b"\xff\xd8")
    assert seen["url"] == cover
    assert seen["headers"]["Referer"] == f"{BASE}/"
    assert json.dumps(seen["headers"]).find("Cookie") == -1

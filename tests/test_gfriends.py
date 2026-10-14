import asyncio
import copy
import json
import logging
from xml.etree import ElementTree as ET

import pytest

from app.config import Settings
from app.db import Database
from app.scrape import fill_jav_folder, refresh_jav_sidecars, resolve_metadata, scrape_job
from app.sources import gfriends
from app.sources.javbus import CACHE_VER

JS_TREE = "https://cdn.jsdelivr.net/gh/gfriends/gfriends@master/Filetree.json"
JS_BASE = "https://cdn.jsdelivr.net/gh/gfriends/gfriends@master/"
RAW_TREE = "https://raw.githubusercontent.com/gfriends/gfriends/master/Filetree.json"
RAW_BASE = "https://raw.githubusercontent.com/gfriends/gfriends/master/"
GOOD = "https://www.javbus.com/pics/actress/2xi_a.jpg"
NOWPRINTING = "https://www.javbus.com/pics/actress/nowprinting.gif"


@pytest.fixture(autouse=True)
def _reset_gfriends_cache():
    gfriends.reset_cache()
    yield
    gfriends.reset_cache()


def _settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    return settings


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []

    async def get(self, url, *args, **kwargs):
        self.urls.append(url)
        body = self.payload(url) if callable(self.payload) else self.payload
        if isinstance(body, Exception):
            raise body
        return _Response(body)


class _Hold:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(gfriends, "site_client", lambda settings: _Hold(client))


def _tree(*actors: tuple[str, str], company: str = "Attackers") -> dict:
    return {"Content": {company: {name: filename for name, filename in actors}}}


def test_exact_match_uses_the_jsdelivr_prefix(tmp_path, monkeypatch):
    client = _Client(_tree(("葵つかさ", "aoi.jpg")))
    seen = []

    def factory(settings):
        seen.append(settings)
        return _Hold(client)

    monkeypatch.setattr(gfriends, "site_client", factory)
    settings = _settings(tmp_path)

    url = asyncio.run(gfriends.actor_photo(settings, "葵つかさ"))

    assert url == JS_BASE + "Content/Attackers/aoi.jpg"
    assert client.urls == [JS_TREE]
    assert seen == [settings]


def test_whitespace_match_is_exact_after_stripping(tmp_path, monkeypatch):
    client = _Client(_tree(("葵 つかさ", "space.jpg"), ("三上悠亜", "mi.jpg")))
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)

    async def run():
        spaced = await gfriends.actor_photo(settings, "葵つかさ")
        ideographic = await gfriends.actor_photo(settings, "葵\u3000つかさ")
        padded = await gfriends.actor_photo(settings, " 葵 つかさ ")
        split = await gfriends.actor_photo(settings, "三 上悠亜")
        exact = await gfriends.actor_photo(settings, "三上悠亜")
        return spaced, ideographic, padded, split, exact

    spaced, ideographic, padded, split, exact = asyncio.run(run())
    assert spaced == JS_BASE + "Content/Attackers/space.jpg"
    assert ideographic == spaced
    assert padded == spaced
    assert split == JS_BASE + "Content/Attackers/mi.jpg"
    assert exact == split
    assert client.urls == [JS_TREE]


def test_miss_returns_empty_without_extra_requests(tmp_path, monkeypatch):
    client = _Client(_tree(("葵つかさ", "aoi.jpg")))
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)

    async def run():
        blank = await gfriends.actor_photo(settings, "   ")
        missing = await gfriends.actor_photo(settings, "没有这个人")
        again = await gfriends.actor_photo(settings, "另一个没有")
        return blank, missing, again

    blank, missing, again = asyncio.run(run())
    assert blank == ""
    assert missing == ""
    assert again == ""
    assert client.urls == [JS_TREE]


def test_substring_does_not_match(tmp_path, monkeypatch):
    client = _Client(_tree(("葵つかさ", "aoi.jpg")))
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)

    async def run():
        return [
            await gfriends.actor_photo(settings, "葵"),
            await gfriends.actor_photo(settings, "つかさ"),
            await gfriends.actor_photo(settings, "葵つかさ子"),
        ]

    assert asyncio.run(run()) == ["", "", ""]


def test_second_source_supplies_its_own_prefix(tmp_path, monkeypatch):
    def payload(url):
        if url == JS_TREE:
            return {"Content": {}}
        return _tree(("葵つかさ", "aoi.jpg"), company="Studio")

    client = _Client(payload)
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)

    async def run():
        first = await gfriends.actor_photo(settings, "葵つかさ")
        gfriends.reset_cache()
        second = await gfriends.actor_photo(settings, "葵つかさ")
        return first, second

    first, second = asyncio.run(run())
    assert first == RAW_BASE + "Content/Studio/aoi.jpg"
    assert second == first
    assert client.urls == [JS_TREE, RAW_TREE]


def test_cache_file_is_reused_until_ttl(tmp_path, monkeypatch):
    assert gfriends.CACHE_TTL == 7 * 24 * 60 * 60
    clock = {"now": 1_700_000_000.0}
    monkeypatch.setattr(gfriends.time, "time", lambda: clock["now"])
    client = _Client(_tree(("葵つかさ", "aoi.jpg")))
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)
    expected = JS_BASE + "Content/Attackers/aoi.jpg"

    async def run():
        first = await gfriends.actor_photo(settings, "葵つかさ")
        second = await gfriends.actor_photo(settings, "葵つかさ")
        gfriends.reset_cache()
        third = await gfriends.actor_photo(settings, "葵つかさ")
        clock["now"] += gfriends.CACHE_TTL
        fourth = await gfriends.actor_photo(settings, "葵つかさ")
        return first, second, third, fourth

    first, second, third, fourth = asyncio.run(run())
    assert first == second == third == fourth == expected
    assert client.urls == [JS_TREE, JS_TREE]
    saved = json.loads((settings.data_dir / gfriends.CACHE_NAME).read_text(encoding="utf-8"))
    assert saved["cdn"] == JS_BASE
    assert saved["names"]["葵つかさ"] == "Content/Attackers/aoi.jpg"
    assert saved["fetched_at"] == clock["now"]


def test_download_failure_is_not_retried_for_ten_minutes(tmp_path, monkeypatch, caplog):
    assert gfriends.FAIL_TTL == 10 * 60
    caplog.set_level(logging.WARNING, logger="app.gfriends")
    clock = {"now": 1_700_000_000.0}
    monkeypatch.setattr(gfriends.time, "time", lambda: clock["now"])
    def boom(url):
        raise RuntimeError("offline")

    client = _Client(boom)
    _patch_client(monkeypatch, client)
    settings = _settings(tmp_path)

    async def run():
        results = [await gfriends.actor_photo(settings, "葵つかさ")]
        results.append(await gfriends.actor_photo(settings, "葵つかさ"))
        clock["now"] += gfriends.FAIL_TTL - 1
        results.append(await gfriends.actor_photo(settings, "葵つかさ"))
        clock["now"] += 1
        results.append(await gfriends.actor_photo(settings, "葵つかさ"))
        return results

    assert asyncio.run(run()) == ["", "", "", ""]
    assert client.urls == [JS_TREE, RAW_TREE, JS_TREE, RAW_TREE]
    assert "10 分钟内不再重试" in caplog.text


def _photo_stub(monkeypatch):
    calls = []

    async def photo(settings, name):
        calls.append(name)
        return {
            "葵つかさ": JS_BASE + "Content/Attackers/aoi.jpg",
            "占位": JS_BASE + "Content/Attackers/blank.jpg",
        }.get(name, "")

    monkeypatch.setattr("app.scrape.gfriends.actor_photo", photo)
    return calls


def _actors():
    return [
        {"name": "葵つかさ", "photo": "", "url": "https://www.javbus.com/star/a"},
        {"name": "有图", "photo": GOOD},
        {"name": "占位", "photo": NOWPRINTING},
        {"name": "查询串", "photo": GOOD + "?file=nowprinting"},
        {"name": "目录", "photo": "https://www.javbus.com/nowprinting/actress/2xi_a.jpg"},
    ]


def _thumbs(path):
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    return {actor.findtext("name"): actor.findtext("thumb") for actor in root.findall("actor")}


def test_archive_fills_missing_photos_without_writing_the_cache(tmp_path, monkeypatch):
    calls = _photo_stub(monkeypatch)
    payload = {
        "code": "SSIS-001",
        "title": "禁欲",
        "release_date": "2021-02-18",
        "cover": "",
        "actors": _actors(),
        "genres": [{"name": "戏剧", "url": "http://example/g"}],
    }
    held = {}

    async def wrapped(settings, db, code):
        meta = await resolve_metadata(settings, db, code)
        held["meta"] = meta
        held["actors"] = copy.deepcopy(meta["actors"])
        return meta

    monkeypatch.setattr("app.scrape.resolve_metadata", wrapped)
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "plain.mp4").write_bytes(b"x" * 8)

    async def run():
        db = Database(settings)
        await db.init()
        key = f"{CACHE_VER}:SSIS-001"
        await db.put_metadata(key, payload)
        result = await scrape_job(settings, db, {
            "code": "SSIS-001",
            "dest": str(dest),
            "title": "禁欲",
        })
        cached = await db.get_metadata_any(key)
        return result, cached

    result, cached = asyncio.run(run())
    archived = settings.media_dir / "202102" / "SSIS-001" / "SSIS-001.nfo"
    thumbs = _thumbs(archived)
    assert result["has_nfo"] is True
    assert calls == ["葵つかさ", "占位"]
    assert held["meta"]["actors"] == held["actors"]
    assert held["meta"]["actors"][0]["photo"] == ""
    assert cached["actors"] == payload["actors"]
    assert cached["actors"][0]["url"] == "https://www.javbus.com/star/a"
    assert cached["genres"] == payload["genres"]
    assert thumbs["葵つかさ"] == JS_BASE + "Content/Attackers/aoi.jpg"
    assert thumbs["有图"] == GOOD
    assert thumbs["占位"] == JS_BASE + "Content/Attackers/blank.jpg"
    assert thumbs["查询串"] == GOOD + "?file=nowprinting"
    assert thumbs["目录"] == "https://www.javbus.com/nowprinting/actress/2xi_a.jpg"
    assert "戏剧" in archived.read_text(encoding="utf-8")


def test_fill_and_refresh_keep_real_photos_and_the_cached_object(tmp_path, monkeypatch):
    calls = _photo_stub(monkeypatch)
    shared = {
        "code": "SSIS-001",
        "title": "禁欲",
        "release_date": "2021-02-18",
        "cover": "",
        "actors": _actors(),
        "genres": [],
    }
    actors = shared["actors"]
    missing = actors[0]
    kept = actors[1]

    async def fake_meta(settings, db, code):
        return shared

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    settings = _settings(tmp_path)
    dest = settings.media_dir / "202102" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "SSIS-001.mp4").write_bytes(b"video-bytes")

    async def run():
        db = Database(settings)
        await db.init()
        filled = await fill_jav_folder(settings, db, dest, "SSIS-001")
        after_fill = _thumbs(dest / "SSIS-001.nfo")
        refreshed = await refresh_jav_sidecars(settings, db, dest, "SSIS-001")
        after_refresh = _thumbs(dest / "SSIS-001.nfo")
        return filled, refreshed, after_fill, after_refresh

    filled, refreshed, after_fill, after_refresh = asyncio.run(run())
    assert filled["has_nfo"] == 1
    assert refreshed["has_nfo"] == 1
    assert calls == ["葵つかさ", "占位", "葵つかさ", "占位"]
    assert shared["actors"] is actors
    assert missing["photo"] == ""
    assert kept["photo"] == GOOD
    assert actors[2]["photo"] == NOWPRINTING
    for thumbs in (after_fill, after_refresh):
        assert thumbs["葵つかさ"] == JS_BASE + "Content/Attackers/aoi.jpg"
        assert thumbs["有图"] == GOOD
        assert thumbs["占位"] == JS_BASE + "Content/Attackers/blank.jpg"
        assert thumbs["查询串"] == GOOD + "?file=nowprinting"
    assert (dest / "SSIS-001.mp4").read_bytes() == b"video-bytes"


def test_gfriends_failure_still_archives_and_rescrapes(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="app.gfriends")
    shared = {
        "code": "SSIS-001",
        "title": "禁欲",
        "release_date": "2021-02-18",
        "cover": "",
        "actors": [{"name": "葵つかさ", "photo": ""}],
        "genres": [],
    }
    calls = {"n": 0}

    async def fake_meta(settings, db, code):
        return shared

    def factory(settings):
        calls["n"] += 1
        raise RuntimeError("offline")

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    monkeypatch.setattr(gfriends, "site_client", factory)
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "plain.mp4").write_bytes(b"x" * 8)

    async def run():
        db = Database(settings)
        await db.init()
        archived = await scrape_job(settings, db, {
            "code": "SSIS-001",
            "dest": str(dest),
            "title": "禁欲",
        })
        folder = settings.media_dir / "202102" / "SSIS-001"
        refreshed = await refresh_jav_sidecars(settings, db, folder, "SSIS-001")
        return archived, refreshed, folder

    archived, refreshed, folder = asyncio.run(run())
    nfo = (folder / "SSIS-001.nfo").read_text(encoding="utf-8")
    assert archived["has_nfo"] is True
    assert refreshed is not None
    assert shared["actors"][0]["photo"] == ""
    assert "葵つかさ" in nfo
    assert "<thumb>" not in nfo
    assert (folder / "SSIS-001.mp4").read_bytes() == b"x" * 8
    assert calls["n"] == 1
    assert "10 分钟内不再重试" in caplog.text

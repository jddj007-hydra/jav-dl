import asyncio
import time

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.routers import western
from app.vr_catalog import (
    FEED_PAGES,
    REFRESH_AFTER,
    Probe,
    crawl_tick,
    feed_caught_up,
    keep_file,
    list_downloadable,
    studio_finished,
)


def test_keep_file_drops_samples_packs_and_flat_noise():
    big = 2_000_000_000
    assert keep_file("virtualtaboo-the-birthday-suit-files-7k_180_LR.mp4", big, False)
    assert keep_file("845-czechvr-3d-7680x3840.mp4", big, False)
    assert not keep_file("VirtualTaboo 合集", big, False)
    assert not keep_file("short-clip.mp4", 50_000_000, False)
    assert keep_file("unknown-size.mp4", None, False)
    assert not keep_file("Katie Cai Dormroom Debaucheries", big, True)
    assert keep_file("DormRoom something VR180 3630p", big, True)


def test_studio_finished_on_short_page_total_or_cap():
    assert studio_finished(1, 0, None)
    assert studio_finished(1, 3, 100)
    assert not studio_finished(1, 12, 100)
    assert studio_finished(9, 12, 99)
    assert studio_finished(120, 12, 5000)
    assert not studio_finished(119, 12, 5000)


def test_feed_stops_on_known_file_or_first_window():
    assert feed_caught_up(1, 12, 500, overlapped=True, subscribed=True)
    assert feed_caught_up(1, 0, None, overlapped=False, subscribed=False)
    assert feed_caught_up(FEED_PAGES, 12, 5000, overlapped=False, subscribed=False)
    assert not feed_caught_up(FEED_PAGES - 1, 12, 5000, overlapped=False, subscribed=False)
    assert not feed_caught_up(FEED_PAGES, 12, 5000, overlapped=False, subscribed=True)
    assert feed_caught_up(120, 12, 5000, overlapped=False, subscribed=True)


def test_catalog_stores_pages_and_stops(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        notify_channel="",
    )
    settings.ensure_dirs()
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    first = [{
        "info_hash": f"{index:040x}",
        "title": f"virtualtaboo-scene-{index}-files-7k_180_LR.mp4",
        "size": "10 GB",
        "size_bytes": 10_000_000_000,
        "heat": 1,
        "date": "2026-10-01",
    } for index in range(1, 12)]
    first.append({
        "info_hash": "b" * 40,
        "title": "VirtualTaboo 合集",
        "size": "40 GB",
        "size_bytes": 40_000_000_000,
        "heat": 1,
        "date": "2026-01-01",
    })
    first[0]["date"] = "2026-10-06"
    first[0]["title"] = "virtualtaboo-birthday-suit-files-7k_180_LR.mp4"
    pages = {
        1: (first, 24, None),
        2: ([{
            "info_hash": "c" * 40,
            "title": "virtualtaboo-older-files-7k_180_LR.mp4",
            "size": "9 GB",
            "size_bytes": 9_000_000_000,
            "heat": 1,
            "date": "2026-09-01",
        }], 24, None),
    }

    async def fetch(query, page):
        assert query == "VirtualTaboo"
        return pages[page]

    async def run():
        db = Database(settings)
        await db.init()
        await crawl_tick(settings, db, pages=1, fetch_page=fetch, probes=(probe,))
        body = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert body["total"] == 12
        assert body["items"][0]["studio"] == "VirtualTaboo"
        assert body["items"][0]["date"] == "2026-10-06"
        assert body["items"][0]["magnet"].startswith("magnet:?xt=urn:btih:")
        assert body["scanning"] is False
        assert all("合集" not in item["title"] for item in body["items"])
        again = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert again["total"] == 12

    asyncio.run(run())


def test_downloadable_route_is_not_a_detail_lookup(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        notify_channel="",
    )
    settings.ensure_dirs()

    async def fetch(_settings, query, page):
        assert page == 1
        return [], 0, None

    monkeypatch.setattr("app.vr_catalog.fetch_magnet_page", fetch)
    app = FastAPI()
    app.include_router(western.router)

    async def prepare():
        db = Database(settings)
        await db.init()
        return db

    app.state.settings = settings
    app.state.db = asyncio.run(prepare())
    response = TestClient(app).get("/api/western/downloadable")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["scanning"] is True
    assert body["progress"].startswith("正在核对")
    assert body["added"] == 0


def _settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        notify_channel="",
    )
    settings.ensure_dirs()
    return settings


def _file(index, date="2026-10-01"):
    return {
        "info_hash": f"{index:040x}",
        "title": f"virtualtaboo-scene-{index}-files-7k_180_LR.mp4",
        "size": "10 GB",
        "size_bytes": 10_000_000_000,
        "heat": 1,
        "date": date,
    }


def test_refresh_stops_on_known_hash_and_can_be_forced(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    known = "a" * 40
    calls = []

    async def fetch(query, page):
        calls.append(page)
        assert page == 1
        return [
            {**_file(1), "info_hash": known, "date": "2026-10-02"},
            _file(2, "2026-10-08"),
        ], 80, None

    async def run():
        db = Database(settings)
        await db.init()
        await db.upsert_vr_magnets([{
            "info_hash": known,
            "title": "virtualtaboo-old-files-7k_180_LR.mp4",
            "size": "8 GB",
            "size_bytes": 8_000_000_000,
            "heat": 1,
            "created": "2026-10-02",
            "studio": "VirtualTaboo",
        }])
        body = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == [1]
        assert body["added"] == 1
        assert body["total"] == 2
        assert body["scanning"] is False
        assert body["items"][0]["date"] == "2026-10-08"
        again = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == [1]
        assert again["added"] == 0
        assert again["total"] == 2
        forced = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, refresh=True,
        )
        assert calls == [1, 1]
        assert forced["added"] == 0
        assert forced["total"] == 2

    asyncio.run(run())


def test_first_look_stops_at_the_feed_window(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    calls = []

    async def fetch(query, page):
        calls.append(page)
        return [_file(page * 100 + index) for index in range(12)], 5000, None

    async def run():
        db = Database(settings)
        await db.init()
        await crawl_tick(settings, db, pages=10, fetch_page=fetch, probes=(probe,))
        body = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == list(range(1, FEED_PAGES + 1))
        assert body["total"] == FEED_PAGES * 12
        assert body["scanning"] is False
        assert body["added"] == 0

    asyncio.run(run())


def test_subscribed_studio_walks_until_the_known_hash(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    known = "b" * 40
    calls = []

    async def fetch(query, page):
        calls.append(page)
        if page < 6:
            return [_file(page * 100 + index) for index in range(12)], 500, None
        return [
            _file(600, "2026-10-09"),
            {**_file(1), "info_hash": known, "date": "2026-01-01"},
        ], 500, None

    async def run():
        db = Database(settings)
        await db.init()
        await db.upsert_vr_magnets([{
            "info_hash": known,
            "title": "virtualtaboo-old-files-7k_180_LR.mp4",
            "size": "8 GB",
            "size_bytes": 8_000_000_000,
            "heat": 1,
            "created": "2026-01-01",
            "studio": "VirtualTaboo",
        }])
        added = await crawl_tick(settings, db, pages=10, fetch_page=fetch, probes=(probe,))
        assert calls == [1, 2, 3, 4, 5, 6]
        assert added == 12 * 5 + 1
        body = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == [1, 2, 3, 4, 5, 6]
        assert body["added"] == 0
        assert body["total"] == added + 1
        assert body["scanning"] is False

    asyncio.run(run())


def test_old_deep_cursor_does_not_resume(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    calls = []

    async def fetch(query, page):
        calls.append(page)
        return [], 0, None

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put("VirtualTaboo", 40, 1000, False, "", 0)
        await crawl_tick(settings, db, pages=2, fetch_page=fetch, probes=(probe,))
        assert calls == [1]
        state = await db.vr_crawl_get("VirtualTaboo")
        assert state["done"] == 1
        assert state["page"] == 1

    asyncio.run(run())


def test_stale_feed_reads_the_head_again(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    calls = []

    async def fetch(query, page):
        calls.append(page)
        assert page == 1
        rows = [_file(1, "2026-10-06")]
        if len(calls) > 1:
            rows.insert(0, _file(9, "2026-10-11"))
        return rows, 12, None

    async def run():
        db = Database(settings)
        await db.init()
        first = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert first["added"] == 1
        assert calls == [1]
        opened = time.time()
        monkeypatch.setattr("app.vr_catalog.time.time", lambda: opened + REFRESH_AFTER + 5)
        second = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == [1, 1]
        assert second["added"] == 1
        assert second["total"] == 2
        assert second["items"][0]["date"] == "2026-10-11"

    asyncio.run(run())


def test_anchor_column_is_added_to_an_old_crawl_table(tmp_path):
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        async with aiosqlite.connect(db.path) as conn:
            await conn.execute(
                """CREATE TABLE vr_crawl (
                    query TEXT PRIMARY KEY,
                    page INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )"""
            )
            await conn.commit()
        await db.init()
        await db.vr_crawl_put("VirtualTaboo", 1, 12, True, "", 1)
        state = await db.vr_crawl_get("VirtualTaboo")
        assert state["anchor"] == 1

    asyncio.run(run())

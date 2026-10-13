import asyncio
import time

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.routers import western
from app.sources.tpdb import TpdbError
from app.vr_catalog import (
    LIST_PAGE,
    MATCH_BATCH,
    PAGE_CAP,
    REFRESH_AFTER,
    Probe,
    crawl_tick,
    feed_caught_up,
    keep_file,
    list_downloadable,
    read_downloadable,
    recent_enough,
    refresh_downloadable,
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
    assert studio_finished(PAGE_CAP, 12, 5000)
    assert not studio_finished(PAGE_CAP - 1, 12, 5000)


def test_recent_enough_keeps_2024_and_drops_older_years():
    assert recent_enough("2024-01-01")
    assert recent_enough("2026-10-13")
    assert recent_enough("")
    assert not recent_enough("2023-12-31")


def test_feed_stops_on_known_file_or_pre_2024():
    assert feed_caught_up(1, 12, 500, overlapped=True)
    assert feed_caught_up(1, 0, None, overlapped=False)
    assert feed_caught_up(2, 12, 5000, overlapped=False, aged=True)
    assert not feed_caught_up(8, 12, 5000, overlapped=False)
    assert feed_caught_up(PAGE_CAP, 12, 5000, overlapped=False)


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


def test_crawl_stops_at_2024_and_skips_older_files(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("VirtualTaboo", "VirtualTaboo", False)
    calls = []

    async def fetch(query, page):
        calls.append(page)
        if page < 3:
            return [_file(page * 100 + index, "2025-06-01") for index in range(12)], 5000, None
        return [
            _file(300, "2024-01-02"),
            _file(301, "2023-12-01"),
            _file(302, "2022-01-01"),
        ], 5000, None

    async def run():
        db = Database(settings)
        await db.init()
        added = await crawl_tick(settings, db, pages=10, fetch_page=fetch, probes=(probe,))
        assert calls == [1, 2, 3]
        assert added == 12 * 2 + 1
        stored = await db.list_vr_magnets("", 100, 0)
        titles = [row["title"] for row in stored[1]]
        assert stored[0] == 25
        assert all("scene-301" not in title and "scene-302" not in title for title in titles)
        body = await list_downloadable(settings, db, 1, probes=(probe,), fetch_page=fetch)
        assert calls == [1, 2, 3]
        assert body["total"] == 25
        assert body["scanning"] is False

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


def _magnet(index, title, created):
    return {
        "info_hash": f"{index:040x}",
        "title": title,
        "size": "8 GB",
        "size_bytes": 8_000_000_000,
        "heat": 1,
        "created": created,
        "studio": "WankzVR",
    }


def test_downloadable_page_fills_metadata_and_keeps_it(tmp_path):
    settings = _settings(tmp_path)
    settings.tpdb_api_key = "token"
    probe = Probe("WankzVR", "WankzVR", False)
    calls = []

    async def fetch(query, page):
        raise AssertionError(f"should not crawl {query} {page}")

    async def match(_settings, title):
        calls.append(title)
        if "miss" in title:
            return "miss", None
        return "hit", {
            "id": "id-" + title,
            "kind": "scene",
            "title": "Official " + title,
            "site": "WankzVR",
            "date": "2023-02-24",
            "cover": "https://cdn.theporndb.net/x.jpg",
            "performers": ["Valentina Nappi"],
            "duration": 56,
        }

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        rows = [
            _magnet(1, "wankzvr-hit-one.mp4", "2026-10-03"),
            _magnet(2, "wankzvr-hit-two.mp4", "2026-10-02"),
            _magnet(3, "wankzvr-miss.mp4", "2026-10-01"),
            _magnet(4, "wankzvr-later.mp4", "2026-09-01"),
        ]
        await db.upsert_vr_magnets(rows)
        body = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert calls == [row["title"] for row in rows[:MATCH_BATCH]]
        assert body["matching"] is True
        assert body["meta_error"] == ""
        hit = body["items"][0]
        assert hit["match"] == "hit"
        assert hit["work_title"] == "Official wankzvr-hit-one.mp4"
        assert hit["performers"] == ["Valentina Nappi"]
        assert hit["release_date"] == "2023-02-24"
        assert hit["duration"] == 56
        assert hit["cover"].endswith("/x.jpg")
        assert hit["background"].endswith("/x.jpg")
        assert hit["id"] == "id-wankzvr-hit-one.mp4"
        assert body["items"][2]["match"] == "miss"
        assert body["items"][3]["match"] == "pending"
        again = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert calls[-1] == "wankzvr-later.mp4"
        assert again["matching"] is False
        assert all(item["match"] in ("hit", "miss") for item in again["items"])
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-hit-one.mp4", "2026-10-03")])
        kept = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert len(calls) == MATCH_BATCH + 1
        assert kept["items"][0]["id"] == "id-wankzvr-hit-one.mp4"
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-renamed.mp4", "2026-10-03")])
        renamed = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert renamed["items"][0]["title"] == "wankzvr-renamed.mp4"
        assert renamed["items"][0]["match"] == "hit"
        assert renamed["items"][0]["id"] == "id-wankzvr-renamed.mp4"

    asyncio.run(run())


def test_metadata_lookup_error_stays_pending(tmp_path):
    settings = _settings(tmp_path)
    settings.tpdb_api_key = "token"
    probe = Probe("WankzVR", "WankzVR", False)

    async def fetch(query, page):
        raise AssertionError("should not crawl")

    async def match(_settings, title):
        raise TpdbError("ThePornDB HTTP 503")

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-hit-one.mp4", "2026-10-03")])
        body = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert body["matching"] is True
        assert body["meta_error"].startswith("元数据暂时没查到")
        assert body["items"][0]["match"] == "pending"
        assert body["items"][0]["id"] == ""

    asyncio.run(run())


def test_metadata_lookup_skips_without_a_token(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("WankzVR", "WankzVR", False)

    async def fetch(query, page):
        raise AssertionError("should not crawl")

    async def match(_settings, title):
        raise AssertionError("should not look up")

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-hit-one.mp4", "2026-10-03")])
        body = await list_downloadable(
            settings, db, 1, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert body["matching"] is False
        assert body["items"][0]["match"] == "pending"

    asyncio.run(run())


def test_old_vr_magnet_table_gains_metadata_columns(tmp_path):
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        async with aiosqlite.connect(db.path) as conn:
            await conn.execute(
                """CREATE TABLE vr_magnet (
                    info_hash TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    size TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    heat INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL DEFAULT '',
                    studio TEXT NOT NULL DEFAULT '',
                    seen_at REAL NOT NULL
                )"""
            )
            await conn.execute(
                """INSERT INTO vr_magnet
                   (info_hash, title, size, size_bytes, heat, created, studio, seen_at)
                   VALUES ('a', 'old.mp4', '1 GB', 1000, 1, '2026-10-01', 'WankzVR', 1)"""
            )
            await conn.commit()
        await db.init()
        await db.save_vr_match("a", "hit", {
            "id": "scene-1",
            "kind": "scene",
            "title": "Old Scene",
            "site": "WankzVR",
            "date": "2024-01-02",
            "cover": "https://cdn.theporndb.net/a.jpg",
            "background": "https://cdn.theporndb.net/wide.jpg",
            "performers": ["A"],
            "duration": 20,
        })
        _total, rows = await db.list_vr_magnets("", 10, 0)
        assert rows[0]["match_state"] == "hit"
        assert rows[0]["tpdb_id"] == "scene-1"
        assert rows[0]["meta_title"] == "Old Scene"
        assert rows[0]["meta_background"] == "https://cdn.theporndb.net/wide.jpg"

    asyncio.run(run())


def test_read_downloadable_pages_the_local_list(tmp_path):
    settings = _settings(tmp_path)
    probe = Probe("WankzVR", "WankzVR", False)

    async def fetch(query, page):
        raise AssertionError(f"should not crawl {query} {page}")

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        rows = [
            _magnet(index, f"file-{index}.mp4", f"2026-10-{index:02d}")
            for index in range(1, LIST_PAGE + 6)
        ]
        await db.upsert_vr_magnets(rows)
        await db.upsert_vr_magnets([
            _magnet(90, "file-miss.mp4", "2026-09-01"),
            _magnet(91, "file-pending.mp4", "2026-09-02"),
        ])
        for row in rows:
            await db.save_vr_match(row["info_hash"], "hit", {
                "id": "id-" + row["title"],
                "kind": "scene",
                "title": "Official " + row["title"],
                "site": "WankzVR",
                "date": "2024-02-02",
                "cover": "",
                "performers": [],
                "duration": 40,
            })
        await db.save_vr_match(f"{90:040x}", "miss", None)
        body = await read_downloadable(
            settings, db, 1, probes=(probe,),
        )
        assert body["page_size"] == LIST_PAGE
        assert body["total"] == LIST_PAGE + 5
        assert body["last_page"] == 2
        assert len(body["items"]) == LIST_PAGE
        assert all(item["match"] == "hit" for item in body["items"])
        assert body["added"] == 0
        second = await read_downloadable(settings, db, 2, probes=(probe,))
        assert second["page"] == 2
        assert len(second["items"]) == 5
        refreshed = await refresh_downloadable(
            settings, db, probes=(probe,), fetch_page=fetch,
        )
        assert refreshed["added"] == 0
        assert refreshed["matched"] == 0

    asyncio.run(run())


def test_refresh_matches_stored_names_without_a_page_view(tmp_path):
    settings = _settings(tmp_path)
    settings.tpdb_api_key = "token"
    probe = Probe("WankzVR", "WankzVR", False)
    calls = []

    async def fetch(query, page):
        raise AssertionError("should not crawl")

    async def match(_settings, title):
        calls.append(title)
        return "hit", {
            "id": "scene-1",
            "kind": "scene",
            "title": "Official",
            "site": "WankzVR",
            "date": "2023-02-24",
            "cover": "https://cdn.theporndb.net/x.jpg",
            "performers": ["Valentina Nappi"],
            "duration": 56,
        }

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-hit-one.mp4", "2026-10-03")])
        shown = await read_downloadable(settings, db, 1, probes=(probe,))
        assert shown["items"] == []
        assert shown["matching"] is True
        result = await refresh_downloadable(
            settings, db, probes=(probe,), fetch_page=fetch, match_title=match,
        )
        assert result["matched"] == 1
        assert calls == ["wankzvr-hit-one.mp4"]
        again = await read_downloadable(settings, db, 1, probes=(probe,))
        assert again["items"][0]["match"] == "hit"
        assert again["items"][0]["work_title"] == "Official"
        assert again["items"][0]["background"].endswith("/x.jpg")
        assert again["matching"] is False

    asyncio.run(run())


def test_refresh_fills_a_missing_wide_cover_once(tmp_path):
    settings = _settings(tmp_path)
    settings.tpdb_api_key = "token"
    probe = Probe("WankzVR", "WankzVR", False)
    calls = []

    async def fetch(query, page):
        raise AssertionError("should not crawl")

    async def match(_settings, title):
        raise AssertionError(title)

    async def detail(_settings, kind, item_id):
        calls.append((kind, item_id))
        if item_id == "scene-miss":
            raise TpdbError("ThePornDB HTTP 503")
        if item_id == "scene-plain":
            return {"cover": "https://cdn.theporndb.net/poster.jpg", "background": ""}
        return {
            "cover": "https://cdn.theporndb.net/poster.jpg",
            "background": "https://cdn.theporndb.net/wide.jpg",
        }

    async def run():
        db = Database(settings)
        await db.init()
        await db.vr_crawl_put(probe.query, 1, 1, True, "", 1)
        await db.upsert_vr_magnets([
            _magnet(1, "wankzvr-wide.mp4", "2026-10-03"),
            _magnet(2, "wankzvr-plain.mp4", "2026-10-02"),
            _magnet(3, "wankzvr-down.mp4", "2026-10-01"),
        ])
        await db.save_vr_match(f"{1:040x}", "hit", {
            "id": "scene-wide", "kind": "scene", "title": "Wide", "cover": "https://cdn.theporndb.net/old.jpg",
        })
        await db.save_vr_match(f"{2:040x}", "hit", {
            "id": "scene-plain", "kind": "scene", "title": "Plain", "cover": "https://cdn.theporndb.net/poster.jpg",
        })
        await db.save_vr_match(f"{3:040x}", "hit", {
            "id": "scene-miss", "kind": "scene", "title": "Down", "cover": "https://cdn.theporndb.net/down.jpg",
        })
        async with aiosqlite.connect(db.path) as conn:
            await conn.execute("UPDATE vr_magnet SET meta_background = ''")
            await conn.commit()
        await refresh_downloadable(
            settings, db, probes=(probe,), fetch_page=fetch, match_title=match, fetch_detail=detail,
        )
        assert calls == [("scene", "scene-wide"), ("scene", "scene-plain"), ("scene", "scene-miss")]
        body = await read_downloadable(settings, db, 1, probes=(probe,))
        by_title = {item["title"]: item for item in body["items"]}
        assert by_title["wankzvr-wide.mp4"]["background"].endswith("/wide.jpg")
        assert by_title["wankzvr-plain.mp4"]["background"].endswith("/poster.jpg")
        assert by_title["wankzvr-down.mp4"]["background"] == ""
        await db.upsert_vr_magnets([_magnet(1, "wankzvr-wide.mp4", "2026-10-03")])
        kept = await read_downloadable(settings, db, 1, probes=(probe,))
        assert kept["items"][0]["background"].endswith("/wide.jpg")
        await refresh_downloadable(
            settings, db, probes=(probe,), fetch_page=fetch, match_title=match, fetch_detail=detail,
        )
        assert calls == [("scene", "scene-wide"), ("scene", "scene-plain"), ("scene", "scene-miss"), ("scene", "scene-miss")]

    asyncio.run(run())


def test_downloadable_route_refresh_does_not_wait_on_magnets(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    async def fetch(_settings, query, page):
        raise AssertionError("route should not crawl")

    monkeypatch.setattr("app.vr_catalog.fetch_magnet_page", fetch)
    app = FastAPI()
    app.include_router(western.router)

    class Jobs:
        def __init__(self):
            self.force = None

        def kick_vr(self, force=False):
            self.force = force

    async def prepare():
        db = Database(settings)
        await db.init()
        return db

    jobs = Jobs()
    app.state.settings = settings
    app.state.db = asyncio.run(prepare())
    app.state.jobs = jobs
    response = TestClient(app).get("/api/western/downloadable", params={"refresh": 1})
    assert response.status_code == 200
    assert response.json()["added"] == 0
    assert jobs.force is True

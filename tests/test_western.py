import asyncio
import time
from pathlib import Path

from app.codes import normalize_code
from app.config import Settings, _overlay, save_user_config
from app.db import Database
from app.downloader.aria2 import Aria2
from app.downloader.jobs import JobManager
from app.ranking import sort_by_heat
from app.routers.images import _allowed
from app.scrape import list_ready_sources
from app.slug import western_slug
from app.sources.javbus import latest_page_url
from app.sources.tpdb import duration_minutes, map_item


def test_latest_page_urls():
    base = "https://www.javbus.com"
    assert latest_page_url(base, "censored", 1) == "https://www.javbus.com/"
    assert latest_page_url(base, "censored", 2) == "https://www.javbus.com/page/2"
    assert latest_page_url(base + "/", "uncensored", 1) == "https://www.javbus.com/uncensored"
    assert latest_page_url(base, "uncensored", 3) == "https://www.javbus.com/uncensored/page/3"


def test_map_tpdb_scene_uses_parent_and_minutes():
    raw = {
        "id": "abc",
        "title": "Room",
        "date": "2024-05-06T00:00:00",
        "duration": 3660,
        "description": "hello",
        "url": "https://example.test/abc",
        "site": {"name": "Brazzers"},
        "posters": {"large": "https://cdn.theporndb.net/p.jpg", "small": "https://cdn.theporndb.net/s.jpg"},
        "background": {"full": "https://cdn.theporndb.net/b.jpg"},
        "performers": [
            {"name": "Alias", "parent": {"name": "Riley Reid", "face": "https://cdn.theporndb.net/f.jpg"}},
            {"name": "Other"},
        ],
        "tags": [{"name": "Feature"}],
    }
    item = map_item(raw, "scene")
    assert item["site"] == "Brazzers"
    assert item["performers"] == ["Riley Reid", "Other"]
    assert item["cover"].endswith("/p.jpg")
    assert item["background"].endswith("/b.jpg")
    assert item["date"] == "2024-05-06"
    assert item["duration"] == "61"
    assert duration_minutes(90) == "1"
    assert duration_minutes(0) == ""
    assert duration_minutes(None) == ""


def test_heat_sort_ignores_single_letter_tags():
    ranked = sort_by_heat([
        {"title": "Scene U C", "heat": 1, "size": "1 GB"},
        {"title": "Plain scene", "heat": 50, "size": "1 GB"},
        {"title": "Studio 合集", "heat": 999, "size": "1 GB"},
    ])
    assert [it["title"] for it in ranked] == ["Plain scene", "Scene U C", "Studio 合集"]
    assert ranked[0]["rank"] == 1
    assert ranked[-1]["pack"] is True


def test_token_hidden_and_blank_keeps_old(tmp_path):
    settings = Settings(data_dir=tmp_path, download_dir=tmp_path / "dl")
    settings.ensure_dirs()
    saved = save_user_config(settings, {"tpdb_api_key": "secret-token"})
    public = saved.public_dict()
    assert public["tpdb_api_key_set"] is True
    assert "secret-token" not in str(public)
    assert "tpdb_api_key" not in public

    updates = {"tpdb_api_key": "", "javbus_base": "https://www.javbus.com"}
    if updates.get("tpdb_api_key") == "":
        updates.pop("tpdb_api_key")
    current = _overlay(Settings(data_dir=tmp_path, download_dir=tmp_path / "dl"))
    merged = save_user_config(current, updates)
    assert merged.tpdb_api_key == "secret-token"


def test_western_slug_is_not_a_code():
    slug = western_slug("AB", "", "123", "id")
    assert normalize_code(slug) is None
    named = western_slug("Brazzers", "2024-01-02", "Late Night")
    assert named.startswith("brazzers-2024-01-02")
    assert normalize_code(named) is None


def test_western_dir_is_not_a_watch_target(tmp_path):
    western = tmp_path / "western"
    western.mkdir()
    video = western / "AB-123.mp4"
    video.write_bytes(b"x" * 80)
    old = time.time() - 1000
    video.touch()
    import os
    os.utime(video, (old, old))
    normal = tmp_path / "SSIS-001.mp4"
    normal.write_bytes(b"x" * 80)
    os.utime(normal, (old, old))
    ready = list_ready_sources(tmp_path, min_bytes=50, settle=0, now=time.time())
    codes = [code for code, _path in ready]
    assert codes == ["SSIS-001"]


def test_non_code_job_skips_scrape(tmp_path):
    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "dl",
            media_dir=tmp_path / "media",
            scrape_enabled=True,
        )
        settings.ensure_dirs()
        db = Database(settings)
        await db.init()
        dest = settings.download_dir / "western" / "brazzers-room"
        dest.mkdir(parents=True)
        (dest / "movie.mp4").write_bytes(b"x" * 80)
        now = time.time()
        job = {
            "id": "job1",
            "code": "brazzers-room",
            "info_hash": "a" * 40,
            "title": "magnet name",
            "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
            "gid": "",
            "status": "complete",
            "dest": str(dest),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 0,
        }
        await db.insert_job(job)
        jobs = JobManager(settings, db, Aria2(settings))
        out = await jobs.maybe_scrape(job)
        assert out["scrape_status"] == "skipped"
        stored = await db.get_job("job1")
        assert stored["scrape_status"] == "skipped"

    asyncio.run(run())


def test_tpdb_image_host_allowed():
    assert _allowed("https://cdn.theporndb.net/p.jpg", "https://www.javbus.com")
    assert _allowed("https://api.metadataapi.net/x.jpg", "https://www.javbus.com")
    assert not _allowed("https://evil.example/p.jpg", "https://www.javbus.com")
    assert not _allowed("https://nottheporndb.net.evil.com/p.jpg", "https://www.javbus.com")

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
from app.western_magnets import parse_release_date, rank_western_magnets, western_search_terms
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


def test_parse_release_date_from_scene_name():
    assert parse_release_date(
        "BrazzersExxtra.22.09.20.Ella.Reese.School.Of.Cock.XXX.1080p.MP4-WRB"
    ) == "2022-09-20"
    assert parse_release_date("clip.2160p.mp4") is None


def test_western_search_terms_use_filename_date():
    terms = western_search_terms(
        "Brazzers Exxtra",
        "School of Cock",
        ["Ella Reese"],
        "2022-09-20",
    )
    assert terms[0] == "BrazzersExxtra 22.09.20"
    assert "BrazzersExxtra 2022.09.20" in terms
    assert terms[-1] == "BrazzersExxtra"


def test_rank_keeps_same_release_day_and_prefers_the_scene():
    items = [
        {
            "title": "BrazzersExxtra.22.09.20.Phoenix.Marie.BrideZZilla.Part.2.XXX.1080p",
            "heat": 500,
            "size": "2 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "BrazzersExxtra.22.09.20.Ella.Reese.School.Of.Cock.XXX.1080p",
            "heat": 20,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
        {
            "title": "BRAZZERS - Brazzers Exxtra - Veruca James",
            "heat": 9000,
            "size": "1 GB",
            "date": "2017-08-23",
            "info_hash": "c" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Brazzers Exxtra",
        "School of Cock",
        ["Ella Reese"],
        "2022-09-20",
    )
    assert match == "date"
    assert ranked[0]["info_hash"].startswith("b")
    assert ranked[0]["release_date"] == "2022-09-20"
    assert all(not item["info_hash"].startswith("c") for item in ranked)


def test_rank_western_prefers_title_overlap():
    items = [
        {"title": "Ella Reese interview", "heat": 900, "size": "1 GB", "info_hash": "a" * 40},
        {"title": "ZeroTolerance.Ella.Reese.Hot.Wife.Creampie.Scene.4", "heat": 20, "size": "2 GB", "info_hash": "b" * 40},
        {"title": "Huge pack Hot Wife Creampie 1-50", "heat": 9999, "size": "40 GB", "info_hash": "c" * 40},
        {"title": "Zero Tolerance.Some.Other.Scene", "heat": 9000, "size": "1 GB", "info_hash": "d" * 40},
    ]
    ranked, match = rank_western_magnets(
        items,
        "Zero Tolerance",
        "Hot Wife Creampie 6 - Scene 4",
        ["Ella Reese"],
    )
    assert match == "title"
    assert ranked[0]["info_hash"].startswith("b")
    assert any(item["info_hash"].startswith("d") for item in ranked)
    assert all(not item["info_hash"].startswith("a") for item in ranked)


def test_site_name_alone_does_not_count_as_the_scene():
    items = [{
        "title": "County Line Rocco Siffredi",
        "heat": 100,
        "size": "1 GB",
        "info_hash": "a" * 40,
    }]
    ranked, match = rank_western_magnets(
        items,
        "Rocco Siffredi",
        "Rocco And Kelly's Prague Adventure",
        ["Andrew A"],
    )
    assert [item["info_hash"] for item in ranked] == ["a" * 40]
    assert match == "site"


def test_western_archive_uses_existing_studio_folder(tmp_path):
    from app.nfo import build_nfo
    from app.western_archive import find_western_videos, studio_dir

    root = tmp_path / "欧美"
    (root / "EvilAngel").mkdir(parents=True)
    assert studio_dir(root, "Evil Angel") == root / "EvilAngel"
    assert studio_dir(root, "New Site").name == "NewSite"

    download = tmp_path / "downloads"
    torrent = download / "Roccos.Teens.Unleashed.6"
    torrent.mkdir(parents=True)
    video = torrent / "scene.mp4"
    video.write_bytes(b"x" * 80)
    src, videos = find_western_videos(
        download,
        download / "western" / "slug",
        "Roccos.Teens.Unleashed.6.XXX",
        min_bytes=50,
    )
    assert src == torrent
    assert videos == [video]

    xml = build_nfo({
        "title": "Rocco's Teens Unleashed",
        "release_date": "2025-08-16",
        "studio": "Evil Angel",
        "actors": ["Baby Doll"],
        "genres": ["Feature"],
        "plot": "hello",
        "uniqueid": "abc",
        "uniqueid_type": "tpdb",
    })
    parsed = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(xml)
    assert parsed.findtext("studio") == "Evil Angel"
    assert parsed.find("uniqueid").attrib["type"] == "tpdb"
    assert parsed.find("uniqueid").text == "abc"
    assert parsed.findtext("plot") == "hello"


def test_scrape_western_moves_file_into_studio(tmp_path):
    import asyncio

    from app.config import Settings
    from app.western_archive import scrape_western_job

    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "dl",
            media_dir=tmp_path / "media",
            western_media_dir=str(tmp_path / "欧美"),
            scrape_min_mb=0,
        )
        settings.ensure_dirs()
        (settings.western_root / "Brazzers").mkdir(parents=True)
        torrent = settings.download_dir / "brazzers.scene.title"
        torrent.mkdir()
        (torrent / "clip.mp4").write_bytes(b"x" * 80)
        result = await scrape_western_job(
            settings,
            {"dest": str(settings.download_dir / "western" / "slug"), "title": "brazzers.scene.title.xxx"},
            {"kind": "western", "site": "Brazzers", "title": "Scene Title", "date": "2024-01-02", "performers": ["Ann Example"]},
        )
        folder = settings.western_root / "Brazzers"
        assert Path(result["path"]) == folder
        assert list(folder.glob("*.mp4"))
        assert list(folder.glob("*.nfo"))
        assert not (torrent / "clip.mp4").exists()

    asyncio.run(run())


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

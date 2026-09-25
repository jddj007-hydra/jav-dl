import asyncio
import time
from datetime import date
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.downloader.jobs import JobManager
from app.scrape import (
    ScrapeError,
    archive_month,
    archive_videos,
    assign_video_names,
    find_code_videos,
    list_ready_sources,
    has_incomplete_files,
    is_incomplete,
    iter_videos,
    looks_like_pack,
    safe_rmtree,
    scrape_job,
)


def test_archive_month_from_release_date():
    assert archive_month("2021-02-18") == "202102"
    assert archive_month("2021/2/18") == "202102"
    assert archive_month("", today=date(2026, 9, 19)) == "202609"
    assert archive_month(None, today=date(2026, 1, 1)) == "202601"


def test_assign_single_and_collision():
    src = Path("/tmp/a.mp4")
    assert assign_video_names("SSIS-001", [src]) == [(src, "SSIS-001.mp4")]
    assert assign_video_names("SSIS-001", [src], {"SSIS-001.mp4"}) == [(src, "SSIS-001-2.mp4")]


def test_assign_multipart_cd():
    a, b = Path("/tmp/a.mkv"), Path("/tmp/b.mkv")
    names = assign_video_names("SSIS-001", [a, b])
    assert [n for _, n in names] == ["SSIS-001-CD1.mkv", "SSIS-001-CD2.mkv"]


def test_iter_videos_skips_small_and_incomplete(tmp_path):
    good = tmp_path / "ok.mp4"
    good.write_bytes(b"x" * 100)
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"x")
    part = tmp_path / "down.mp4.xltd"
    part.write_bytes(b"x" * 100)
    live = tmp_path / "live.mp4"
    live.write_bytes(b"x" * 100)
    (tmp_path / "live.mp4.xltd").write_bytes(b"x")
    found = iter_videos(tmp_path, min_bytes=50)
    assert found == [good]
    assert is_incomplete(live)
    assert has_incomplete_files(tmp_path)


def test_looks_like_pack():
    assert looks_like_pack("SSIS 001-100")
    assert looks_like_pack("SSIS.FHD.Pack.001-100")
    assert looks_like_pack("18部合集SSIS")
    assert not looks_like_pack("SSIS-001-UC")
    assert not looks_like_pack("ssis-001-uncensored")


def test_find_code_videos_in_xunlei_named_folder(tmp_path):
    dest = tmp_path / "jav-dl" / "SSIS-001"
    torrent = tmp_path / "ssis-001-uncensored"
    torrent.mkdir()
    video = torrent / "foo.mp4"
    video.write_bytes(b"x" * 80)
    (tmp_path / "云盘缓存文件").mkdir()
    (tmp_path / "云盘缓存文件" / "stub.mp4").write_bytes(b"x" * 80)
    pack = tmp_path / "SSIS 001-100"
    pack.mkdir()
    (pack / "SSIS-001.mp4").write_bytes(b"x" * 80)
    src, videos = find_code_videos("SSIS-001", dest, tmp_path, min_bytes=50)
    assert src == torrent
    assert videos == [video]


def test_find_code_videos_prefers_dest(tmp_path):
    dest = tmp_path / "SSIS-001"
    dest.mkdir()
    video = dest / "a.mp4"
    video.write_bytes(b"x" * 80)
    other = tmp_path / "ssis-001-uncensored"
    other.mkdir()
    (other / "b.mp4").write_bytes(b"x" * 80)
    src, videos = find_code_videos("SSIS-001", dest, tmp_path, min_bytes=50)
    assert src == dest
    assert videos == [video]


def test_list_ready_sources_skips_cache_and_incomplete(tmp_path):
    torrent = tmp_path / "ssis-001-uncensored"
    torrent.mkdir()
    video = torrent / "play.mp4"
    video.write_bytes(b"x" * 80)
    cache = tmp_path / "云盘缓存文件"
    cache.mkdir()
    (cache / "stub.mp4").write_bytes(b"x" * 80)
    busy = tmp_path / "IPX-643-HD"
    busy.mkdir()
    (busy / "a.mp4").write_bytes(b"x" * 80)
    (busy / "a.mp4.xltd").write_bytes(b"x")
    now = video.stat().st_mtime + 120
    ready = list_ready_sources(tmp_path, min_bytes=50, settle=60, now=now)
    assert ready == [("SSIS-001", torrent)]


def test_list_ready_sources_waits_for_settle(tmp_path):
    torrent = tmp_path / "SSIS-001"
    torrent.mkdir()
    video = torrent / "a.mp4"
    video.write_bytes(b"x" * 80)
    now = video.stat().st_mtime + 10
    assert list_ready_sources(tmp_path, min_bytes=50, settle=60, now=now) == []


def test_find_code_videos_missing(tmp_path):
    try:
        find_code_videos("SSIS-001", tmp_path / "missing", tmp_path, min_bytes=0)
    except ScrapeError as e:
        assert "没有可归档的视频" in str(e)
    else:
        raise AssertionError("expected ScrapeError")


def test_archive_videos_renames_and_nfo(tmp_path):
    src_dir = tmp_path / "dl" / "SSIS-001"
    src_dir.mkdir(parents=True)
    video = src_dir / "广告www.foo.com-SSIS-001.mp4"
    video.write_bytes(b"video")
    sub = src_dir / "广告www.foo.com-SSIS-001.zh.srt"
    sub.write_text("1", encoding="utf-8")
    dest = tmp_path / "media" / "202102" / "SSIS-001"
    written = archive_videos("SSIS-001", [video], dest, "<movie/>")
    assert written[0].name == "SSIS-001.mp4"
    assert (dest / "SSIS-001.mp4").is_file()
    assert (dest / "SSIS-001.nfo").read_text(encoding="utf-8") == "<movie/>"
    assert (dest / "SSIS-001.zh.srt").is_file()
    assert not video.exists()


def test_scrape_job_archives_without_cover(tmp_path, monkeypatch):
    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "禁欲",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    data = tmp_path / "data"
    root = tmp_path / "dl"
    dest = root / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    media = tmp_path / "media"
    settings = Settings(
        data_dir=data,
        download_dir=root,
        media_dir=media,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        result = await scrape_job(
            settings,
            db,
            {"code": "SSIS-001", "dest": str(dest)},
        )
        assert result["path"] == "202102/SSIS-001"
        assert (media / "202102" / "SSIS-001" / "SSIS-001.mp4").is_file()
        assert (media / "202102" / "SSIS-001" / "SSIS-001.nfo").is_file()
        assert not dest.exists()

    asyncio.run(run())


def _job(dest: Path) -> dict:
    now = time.time()
    return {
        "id": "abc123",
        "code": "SSIS-001",
        "info_hash": "a" * 40,
        "title": "t",
        "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
        "gid": "g",
        "status": "complete",
        "dest": str(dest),
        "error": None,
        "created_at": now,
        "updated_at": now,
        "cleaned": 1,
    }


def test_maybe_scrape_waits_for_settle(tmp_path):
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "a.mp4").write_bytes(b"x" * 80)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=60,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        job = _job(dest)
        await db.insert_job(job)
        mgr = JobManager(settings, db, object())
        out = await mgr.maybe_scrape(job)
        assert out["scrape_status"] == "waiting"

    asyncio.run(run())


def test_find_code_videos_loose_file_is_the_file_itself(tmp_path):
    bucket = tmp_path / "xunlei"
    bucket.mkdir()
    video = bucket / "SSIS-001.mp4"
    video.write_bytes(b"x" * 80)
    src, videos = find_code_videos("SSIS-001", tmp_path / "missing", tmp_path, min_bytes=50)
    assert src == video
    assert videos == [video]


def test_safe_rmtree_keeps_shared_buckets(tmp_path):
    root = tmp_path / "dl"
    for name in ("xunlei", "jav-dl", "western"):
        bucket = root / name
        bucket.mkdir(parents=True)
        (bucket / "keep.txt").write_text("x", encoding="utf-8")
        safe_rmtree(bucket, root)
        assert bucket.is_dir()
        assert (bucket / "keep.txt").is_file()
    leaf = root / "xunlei" / "SSIS-001"
    leaf.mkdir()
    safe_rmtree(leaf, root)
    assert not leaf.exists()
    assert (root / "xunlei").is_dir()


def test_loose_file_archives_while_another_download_is_busy(tmp_path, monkeypatch):
    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "禁欲",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    root = tmp_path / "dl"
    video = root / "xunlei" / "SSIS-001.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x" * 80)
    busy = root / "IPX-001"
    busy.mkdir()
    (busy / "a.mp4").write_bytes(b"x" * 80)
    (busy / "a.mp4.aria2").write_bytes(b"ctl")
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=root,
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=0,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        job = _job(root / "jav-dl" / "SSIS-001")
        job["status"] = "complete"
        job["cleaned"] = 1
        await db.insert_job(job)
        mgr = JobManager(settings, db, object())
        out = await mgr.maybe_scrape(job)
        assert out["scrape_status"] == "archived"
        assert video.parent.is_dir()
        assert not video.exists()
        assert (busy / "a.mp4.aria2").is_file()
        assert (settings.media_dir / "202102" / "SSIS-001" / "SSIS-001.mp4").is_file()

    asyncio.run(run())


def test_scrape_job_moves_files_off_the_event_loop(tmp_path, monkeypatch):
    seen = []
    real = asyncio.to_thread

    async def wrapped(fn, *args, **kwargs):
        seen.append(getattr(fn, "__name__", ""))
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", wrapped)

    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "禁欲",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    root = tmp_path / "dl"
    dest = root / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=root,
        media_dir=tmp_path / "media",
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        await scrape_job(settings, db, {"code": "SSIS-001", "dest": str(dest)})

    asyncio.run(run())
    assert "find_code_videos" in seen
    assert "_commit_jav" in seen


def test_watch_logs_a_scrape_failure_once(tmp_path, monkeypatch, caplog):
    import logging

    async def boom(*args, **kwargs):
        raise ScrapeError("元数据失败")

    monkeypatch.setattr("app.downloader.jobs.scrape_job", boom)
    root = tmp_path / "dl"
    folder = root / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "a.mp4").write_bytes(b"x" * 80)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=root,
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=0,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    caplog.set_level(logging.WARNING, logger="app.downloader.jobs")

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        await mgr.watch_downloads()
        await mgr.watch_downloads()

    asyncio.run(run())
    messages = [rec.message for rec in caplog.records if "监控归档失败" in rec.message]
    assert len(messages) == 1
    assert "元数据失败" in messages[0]


def test_maybe_scrape_skipped_when_disabled(tmp_path):
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=False,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        job = _job(dest)
        await db.insert_job(job)
        mgr = JobManager(settings, db, object())
        out = await mgr.maybe_scrape(job)
        assert out["scrape_status"] == "skipped"

    asyncio.run(run())

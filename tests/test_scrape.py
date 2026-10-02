import asyncio
import os
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
    newer_unmatched_videos,
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


def test_scrape_job_sends_jav_vr_to_vrporn(tmp_path, monkeypatch):
    async def fake_meta(settings, db, code):
        return {
            "code": "DSVR-1124",
            "title": "VR",
            "release_date": "2022-05-01",
            "cover": "",
            "actors": [],
            "genres": [],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    data = tmp_path / "data"
    root = tmp_path / "dl"
    dest = root / "DSVR-1124"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    media = tmp_path / "media"
    vrporn = tmp_path / "vrporn"
    settings = Settings(
        data_dir=data,
        download_dir=root,
        media_dir=media,
        vr_media_dir=str(vrporn),
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        result = await scrape_job(
            settings,
            db,
            {"code": "DSVR-1124", "dest": str(dest)},
        )
        assert result["path"] == "DSVR/DSVR-1124"
        assert (vrporn / "jav" / "DSVR" / "DSVR-1124" / "DSVR-1124.mp4").is_file()
        assert (vrporn / "jav" / "DSVR" / "DSVR-1124" / "DSVR-1124.nfo").is_file()
        assert not (media / "202205" / "DSVR-1124").exists()

    asyncio.run(run())


def test_fill_jav_folder_writes_nfo_and_poster(tmp_path, monkeypatch):
    from app.scrape import fill_jav_folder

    async def fake_meta(settings, db, code):
        return {
            "code": "DSVR-1124",
            "title": "Headset",
            "release_date": "2022-05-01",
            "cover": "https://www.javbus.com/pics/cover/x_b.jpg",
            "actors": [{"name": "葵"}],
            "genres": [],
        }

    async def fake_cover(settings, url, referer=None):
        return b"jpg-bytes"

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    monkeypatch.setattr("app.scrape.fetch_cover_bytes", fake_cover)
    dest = tmp_path / "jav" / "DSVR" / "DSVR-1124"
    dest.mkdir(parents=True)
    (dest / "DSVR-1124.mp4").write_bytes(b"x" * 8)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        row = await fill_jav_folder(settings, db, dest, "DSVR-1124")
        assert row["has_nfo"] == 1
        assert row["has_poster"] == 1
        assert (dest / "DSVR-1124.nfo").is_file()
        assert (dest / "poster.jpg").read_bytes() == b"jpg-bytes"
        assert "Headset" in (dest / "DSVR-1124.nfo").read_text(encoding="utf-8")

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


class _Owned:
    async def get(self, code):
        return {"code": code, "has_video": 1, "path": "202102/SSIS-001"}


def test_owned_code_without_a_new_file_counts_as_archived(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=0,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        job = _job(tmp_path / "dl" / "gone")
        job["created_at"] = time.time() - 30
        await db.insert_job(job)
        mgr = JobManager(settings, db, object(), library=_Owned())
        out = await mgr.maybe_scrape(job)
        assert out["scrape_status"] == "archived"
        assert out["archive_path"] == "202102/SSIS-001"

    asyncio.run(run())


def test_owned_code_stays_open_when_a_new_file_has_no_code(tmp_path):
    root = tmp_path / "dl"
    video = root / "xunlei" / "no-code-name.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x" * 80)
    old = root / "old-junk.mp4"
    old.write_bytes(b"x" * 40)
    now = time.time()
    os.utime(video, (now, now))
    os.utime(old, (now - 3600, now - 3600))
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=root,
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=0,
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    assert newer_unmatched_videos(root, now - 30, 0) == [video]

    async def run():
        db = Database(settings)
        await db.init()
        job = _job(root / "SSIS-001")
        job["created_at"] = now - 30
        await db.insert_job(job)
        mgr = JobManager(settings, db, object(), library=_Owned())
        out = await mgr.maybe_scrape(job)
        assert out["scrape_status"] == "error"
        assert out["scrape_error"] == "没有可归档的视频"
        assert video.is_file()

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


def _solid_jpeg(width: int, height: int, left_color: tuple[int, int, int], right_color: tuple[int, int, int], split: int) -> bytes:
    import io

    from PIL import Image

    image = Image.new("RGB", (width, height), left_color)
    if 0 < split < width:
        image.paste(right_color, (split, 0, width, height))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_write_images_keeps_fanart_and_crops_poster_from_the_right(tmp_path):
    import io

    from PIL import Image

    from app.scrape import write_images

    width, height = 900, 600
    crop_w = (height * 2) // 3
    raw = _solid_jpeg(width, height, (220, 20, 20), (20, 20, 220), width - crop_w)
    dest = tmp_path / "SSIS-001"
    dest.mkdir()

    has_poster, has_fanart = write_images(dest, raw)

    assert has_poster and has_fanart
    assert (dest / "fanart.jpg").read_bytes() == raw
    poster_bytes = (dest / "poster.jpg").read_bytes()
    assert poster_bytes != raw
    with Image.open(io.BytesIO(poster_bytes)) as poster:
        assert poster.size == (crop_w, height)
        assert abs(poster.size[0] / poster.size[1] - 2 / 3) < 0.01
        pixel = poster.getpixel((crop_w - 8, height // 2))
    assert pixel[2] > 180 and pixel[0] < 80


def test_write_images_copies_original_when_not_wider_than_poster(tmp_path):
    from app.scrape import write_images

    portrait = _solid_jpeg(200, 600, (10, 180, 40), (10, 180, 40), 200)
    exact = _solid_jpeg(400, 600, (30, 30, 30), (30, 30, 30), 400)
    for name, raw in (("portrait", portrait), ("exact", exact)):
        dest = tmp_path / name
        dest.mkdir()
        write_images(dest, raw)
        assert (dest / "fanart.jpg").read_bytes() == raw
        assert (dest / "poster.jpg").read_bytes() == raw


def test_write_images_copies_original_when_decode_fails(tmp_path):
    from app.scrape import write_images

    raw = b"not-a-jpeg"
    dest = tmp_path / "bad"
    dest.mkdir()

    has_poster, has_fanart = write_images(dest, raw)

    assert has_poster and has_fanart
    assert (dest / "fanart.jpg").read_bytes() == raw
    assert (dest / "poster.jpg").read_bytes() == raw


def test_write_images_does_not_replace_existing_files(tmp_path):
    from app.scrape import write_images

    dest = tmp_path / "kept"
    dest.mkdir()
    (dest / "poster.jpg").write_bytes(b"old-poster")
    (dest / "fanart.jpg").write_bytes(b"old-fanart")
    raw = _solid_jpeg(900, 600, (220, 20, 20), (20, 20, 220), 500)

    write_images(dest, raw)

    assert (dest / "poster.jpg").read_bytes() == b"old-poster"
    assert (dest / "fanart.jpg").read_bytes() == b"old-fanart"


class _ImageHold:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc):
        return False


class _ImageClient:
    def __init__(self, bodies: dict[str, bytes | Exception], status: dict[str, int] | None = None):
        self.bodies = bodies
        self.status = status or {}
        self.urls: list[str] = []

    async def get(self, url, headers=None):
        import httpx

        self.urls.append(url)
        body = self.bodies.get(url, b"")
        if isinstance(body, Exception):
            raise body
        code = self.status.get(url, 200)
        return httpx.Response(code, content=body, request=httpx.Request("GET", url))


def _scrape_settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    return settings


def test_scrape_job_writes_extrafanart_and_skips_a_failed_sample(tmp_path, monkeypatch, caplog):
    import logging

    samples = [
        {"full": "https://img.example/a.jpg", "thumb": "https://img.example/a-t.jpg"},
        {"full": "https://img.example/b.jpg"},
        {"full": "https://img.example/c.jpg"},
        {"full": ""},
    ]
    client = _ImageClient(
        {"https://img.example/a.jpg": b"pic-a", "https://img.example/c.jpg": b"pic-c"},
        {"https://img.example/b.jpg": 404},
    )
    monkeypatch.setattr("app.scrape.site_client", lambda settings: _ImageHold(client))

    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "样图",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
            "samples": samples,
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    settings = _scrape_settings(tmp_path)
    caplog.set_level(logging.WARNING, logger="app.scrape")

    async def run():
        db = Database(settings)
        await db.init()
        await scrape_job(settings, db, {"code": "SSIS-001", "dest": str(dest)})

    asyncio.run(run())
    archived = tmp_path / "media" / "202102" / "SSIS-001"
    extra = archived / "extrafanart"
    assert (archived / "SSIS-001.mp4").is_file()
    assert (archived / "SSIS-001.nfo").is_file()
    assert (extra / "fanart-01.jpg").read_bytes() == b"pic-a"
    assert not (extra / "fanart-02.jpg").exists()
    assert (extra / "fanart-03.jpg").read_bytes() == b"pic-c"
    assert client.urls == [
        "https://img.example/a.jpg",
        "https://img.example/b.jpg",
        "https://img.example/c.jpg",
    ]
    assert list((settings.data_dir / "img_cache").iterdir()) == []
    assert any("预览图跳过 fanart-02.jpg" in rec.message for rec in caplog.records)


def test_scrape_job_caps_extrafanart_at_twenty(tmp_path, monkeypatch):
    samples = [{"full": f"https://img.example/{i:02d}.jpg"} for i in range(1, 23)]
    client = _ImageClient({item["full"]: f"p{i}".encode() for i, item in enumerate(samples, start=1)})
    monkeypatch.setattr("app.scrape.site_client", lambda settings: _ImageHold(client))

    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "很多样图",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
            "samples": samples,
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        await scrape_job(settings, db, {"code": "SSIS-001", "dest": str(dest)})

    asyncio.run(run())
    extra = tmp_path / "media" / "202102" / "SSIS-001" / "extrafanart"
    names = sorted(path.name for path in extra.iterdir())
    assert names == [f"fanart-{i:02d}.jpg" for i in range(1, 21)]
    assert len(client.urls) == 20
    assert "https://img.example/21.jpg" not in client.urls


def test_scrape_job_without_samples_skips_extrafanart(tmp_path, monkeypatch):
    called = {"n": 0}

    def boom(settings):
        called["n"] += 1
        raise AssertionError("没有样图时不应下载")

    monkeypatch.setattr("app.scrape.site_client", boom)

    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "无样图",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        await scrape_job(settings, db, {"code": "SSIS-001", "dest": str(dest)})

    asyncio.run(run())
    archived = tmp_path / "media" / "202102" / "SSIS-001"
    assert (archived / "SSIS-001.mp4").is_file()
    assert not (archived / "extrafanart").exists()
    assert called["n"] == 0


def test_scrape_job_keeps_existing_extrafanart(tmp_path, monkeypatch):
    client = _ImageClient({"https://img.example/2.jpg": b"new-2"})
    monkeypatch.setattr("app.scrape.site_client", lambda settings: _ImageHold(client))

    async def fake_meta(settings, db, code):
        return {
            "code": "SSIS-001",
            "title": "已有样图",
            "release_date": "2021-02-18",
            "cover": "",
            "actors": [],
            "genres": [],
            "samples": [
                {"full": "https://img.example/1.jpg"},
                {"full": "https://img.example/2.jpg"},
            ],
        }

    monkeypatch.setattr("app.scrape.resolve_metadata", fake_meta)
    kept = tmp_path / "media" / "202102" / "SSIS-001" / "extrafanart"
    kept.mkdir(parents=True)
    (kept / "fanart-01.jpg").write_bytes(b"keep-me")
    dest = tmp_path / "dl" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "foo.mp4").write_bytes(b"x" * 8)
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        await scrape_job(settings, db, {"code": "SSIS-001", "dest": str(dest)})

    asyncio.run(run())
    assert (kept / "fanart-01.jpg").read_bytes() == b"keep-me"
    assert (kept / "fanart-02.jpg").read_bytes() == b"new-2"
    assert client.urls == ["https://img.example/2.jpg"]


def _meta_with_cover(samples):
    async def fake_meta(settings, db, code):
        return {
            "code": code,
            "title": "新标题",
            "release_date": "2021-02-18",
            "cover": "https://img.example/cover.jpg",
            "actors": [{"name": "葵", "photo": "https://www.javbus.com/pics/actress/2xi_a.jpg"}],
            "genres": [],
            "samples": samples,
        }

    return fake_meta


def test_fill_jav_folder_does_not_replace_existing_sidecars(tmp_path, monkeypatch):
    from app.scrape import fill_jav_folder

    client = _ImageClient({"https://img.example/2.jpg": b"new-sample"})
    monkeypatch.setattr("app.scrape.site_client", lambda settings: _ImageHold(client))
    monkeypatch.setattr(
        "app.scrape.resolve_metadata",
        _meta_with_cover([
            {"full": "https://img.example/1.jpg"},
            {"full": "https://img.example/2.jpg"},
        ]),
    )

    async def fake_cover(settings, url, referer=None):
        return _solid_jpeg(900, 600, (220, 20, 20), (20, 20, 220), 500)

    monkeypatch.setattr("app.scrape.fetch_cover_bytes", fake_cover)
    dest = tmp_path / "media" / "202102" / "SSIS-001"
    extra = dest / "extrafanart"
    extra.mkdir(parents=True)
    (dest / "SSIS-001.mp4").write_bytes(b"video-bytes")
    (dest / "poster.jpg").write_bytes(b"old-poster")
    (dest / "fanart.jpg").write_bytes(b"old-fanart")
    (extra / "fanart-01.jpg").write_bytes(b"old-sample")
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        row = await fill_jav_folder(settings, db, dest, "SSIS-001")
        assert row["has_nfo"] == 1
        assert row["has_poster"] == 1

    asyncio.run(run())
    assert (dest / "poster.jpg").read_bytes() == b"old-poster"
    assert (dest / "fanart.jpg").read_bytes() == b"old-fanart"
    assert (extra / "fanart-01.jpg").read_bytes() == b"old-sample"
    assert (extra / "fanart-02.jpg").read_bytes() == b"new-sample"
    assert "新标题" in (dest / "SSIS-001.nfo").read_text(encoding="utf-8")
    assert client.urls == ["https://img.example/2.jpg"]
    assert (dest / "SSIS-001.mp4").read_bytes() == b"video-bytes"


def test_rescrape_rewrites_sidecars_and_keeps_the_video(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from app.library import Library

    raw = _solid_jpeg(900, 600, (220, 20, 20), (20, 20, 220), 500)
    client = _ImageClient({"https://img.example/1.jpg": b"fresh-sample"})
    monkeypatch.setattr("app.scrape.site_client", lambda settings: _ImageHold(client))
    monkeypatch.setattr(
        "app.scrape.resolve_metadata",
        _meta_with_cover([{"full": "https://img.example/1.jpg"}]),
    )

    async def fake_cover(settings, url, referer=None):
        return raw

    monkeypatch.setattr("app.scrape.fetch_cover_bytes", fake_cover)
    dest = tmp_path / "media" / "202102" / "SSIS-001"
    extra = dest / "extrafanart"
    extra.mkdir(parents=True)
    (dest / "SSIS-001.mp4").write_bytes(b"video-bytes")
    (dest / "poster.jpg").write_bytes(b"old-poster")
    (dest / "fanart.jpg").write_bytes(b"old-poster")
    (dest / "SSIS-001.nfo").write_text("<movie><title>old</title></movie>", encoding="utf-8")
    (extra / "fanart-01.jpg").write_bytes(b"old-sample")
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        library = Library(settings, db)
        await library.refresh()
        await db.insert_job(_job(settings.download_dir / "missing"))
        out = await JobManager(settings, db, object(), library=library).rescrape("abc123")
        assert out["scrape_status"] == "archived"
        stored = await db.get_library("SSIS-001")
        assert "新标题" in stored["title"]

    asyncio.run(run())
    assert (dest / "SSIS-001.mp4").read_bytes() == b"video-bytes"
    assert (dest / "fanart.jpg").read_bytes() == raw
    poster = (dest / "poster.jpg").read_bytes()
    assert poster not in (b"old-poster", raw)
    with Image.open(io.BytesIO(poster)) as image:
        assert image.size == (400, 600)
    nfo = (dest / "SSIS-001.nfo").read_text(encoding="utf-8")
    assert "新标题" in nfo
    assert "https://www.javbus.com/pics/actress/2xi_a.jpg" in nfo
    assert (extra / "fanart-01.jpg").read_bytes() == b"fresh-sample"
    assert client.urls == ["https://img.example/1.jpg"]
    names = {path.name for path in dest.iterdir()}
    assert names == {"SSIS-001.mp4", "SSIS-001.nfo", "poster.jpg", "fanart.jpg", "extrafanart"}


def test_rescrape_leaves_sidecars_when_cover_download_fails(tmp_path, monkeypatch):
    from app.library import Library
    from app.scrape import ScrapeError

    monkeypatch.setattr(
        "app.scrape.resolve_metadata",
        _meta_with_cover([{"full": "https://img.example/1.jpg"}]),
    )

    async def fake_cover(settings, url, referer=None):
        raise ScrapeError("封面下载失败")

    monkeypatch.setattr("app.scrape.fetch_cover_bytes", fake_cover)
    dest = tmp_path / "media" / "202102" / "SSIS-001"
    dest.mkdir(parents=True)
    (dest / "SSIS-001.mp4").write_bytes(b"video-bytes")
    (dest / "poster.jpg").write_bytes(b"old-poster")
    (dest / "fanart.jpg").write_bytes(b"old-fanart")
    (dest / "SSIS-001.nfo").write_text("<movie><title>old</title></movie>", encoding="utf-8")
    settings = _scrape_settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        library = Library(settings, db)
        await library.refresh()
        await db.insert_job(_job(settings.download_dir / "missing"))
        out = await JobManager(settings, db, object(), library=library).rescrape("abc123")
        assert out["scrape_status"] == "error"
        assert "封面" in (out["scrape_error"] or "")

    asyncio.run(run())
    assert (dest / "SSIS-001.mp4").read_bytes() == b"video-bytes"
    assert (dest / "poster.jpg").read_bytes() == b"old-poster"
    assert (dest / "fanart.jpg").read_bytes() == b"old-fanart"
    assert "old" in (dest / "SSIS-001.nfo").read_text(encoding="utf-8")

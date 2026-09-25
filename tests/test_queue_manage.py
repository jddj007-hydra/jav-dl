import asyncio
import time

import pytest

from app.config import Settings
from app.db import Database
from app.downloader.jobs import JobManager


def _settings(tmp_path, **kwargs):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=True,
        scrape_settle_seconds=0,
        scrape_min_mb=0,
        tpdb_api_key="token",
        western_media_dir=str(tmp_path / "western"),
        **kwargs,
    )
    settings.ensure_dirs()
    return settings


def _row(dest: str, **overrides) -> dict:
    now = time.time()
    row = {
        "id": "job1",
        "code": "SSIS-001",
        "info_hash": "a" * 40,
        "title": "t",
        "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
        "gid": "",
        "status": "complete",
        "dest": dest,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "cleaned": 1,
        "backend": "aria2",
        "scrape_status": "",
        "scrape_error": None,
        "archive_path": "",
    }
    row.update(overrides)
    return row


def test_retrying_scrape_stays_busy(tmp_path):
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_row(str(settings.download_dir)))
        await db.update_job("job1", scrape_status="error", scrape_error="没有可归档的视频")
        mgr = JobManager(settings, db, object())
        assert await mgr._busy_codes() == {"SSIS-001"}
        await db.update_job("job1", scrape_status="archived")
        assert await mgr._busy_codes() == set()

    asyncio.run(run())


def test_watch_western_skips_a_queue_job(tmp_path, monkeypatch):
    seen = []

    async def grab(settings, src):
        seen.append(src)

    monkeypatch.setattr("app.downloader.jobs.scrape_western_source", grab)
    settings = _settings(tmp_path)
    video = settings.download_dir / "Brazzers.24.01.02.Ann.Example.mp4"
    video.write_bytes(b"x" * 80)
    slug = settings.download_dir / "western" / "slug"
    slug.mkdir(parents=True)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_row(
            str(slug),
            code="brazzers-ann",
            title="Brazzers.24.01.02.Ann.Example.XXX",
        ))
        await db.update_job("job1", scrape_status="error", scrape_error="没有可归档的视频")
        mgr = JobManager(settings, db, object())
        await mgr.watch_western()
        assert seen == []
        await db.update_job("job1", scrape_status="archived")
        await mgr.watch_western()
        assert seen == [video]

    asyncio.run(run())


def test_delete_finished_job_and_reject_active(tmp_path):
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_row(str(settings.download_dir), id="done", status="complete"))
        await db.insert_job(_row(
            str(settings.download_dir),
            id="run",
            status="downloading",
            code="SSIS-002",
            info_hash="b" * 40,
        ))
        await db.insert_job(_row(
            str(settings.download_dir),
            id="old",
            status="cancelled",
            code="SSIS-003",
            info_hash="c" * 40,
        ))
        mgr = JobManager(settings, db, object())
        await mgr.delete("done")
        assert await db.get_job("done") is None
        with pytest.raises(ValueError):
            await mgr.delete("run")
        assert await mgr.clear_finished("finished") == 1
        assert [job["id"] for job in await db.list_jobs()] == ["run"]

    asyncio.run(run())


def test_rescrape_clears_the_error_and_waits(tmp_path):
    settings = _settings(tmp_path)
    settings.scrape_settle_seconds = 60
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir()
    (dest / "a.mp4").write_bytes(b"x" * 80)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_row(str(dest)))
        await db.update_job("job1", scrape_status="error", scrape_error="旧失败")
        mgr = JobManager(settings, db, object())
        out = await mgr.rescrape("job1")
        assert out["scrape_status"] == "waiting"
        assert out["scrape_error"] is None
        stored = await db.get_job("job1")
        assert stored["scrape_error"] is None

    asyncio.run(run())


def test_clear_rejects_unknown_status(tmp_path):
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        with pytest.raises(ValueError):
            await mgr.clear_finished("downloading")

    asyncio.run(run())

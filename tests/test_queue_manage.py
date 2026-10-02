import asyncio
import time
from types import SimpleNamespace

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


def test_public_title_target_uses_code_or_sidecar(tmp_path):
    from app.western_archive import write_sidecar

    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        jav = await mgr.public(_row(str(settings.download_dir)))
        assert jav["kind"] == "jav"
        assert jav["tpdb_id"] == ""

        dest = settings.download_dir / "western" / "ann"
        write_sidecar(dest, {"kind": "western", "tpdb_id": "scene-1", "title": "Ann"})
        west = await mgr.public(_row(str(dest), code="ann-example", title="Ann"))
        assert west["kind"] == "western"
        assert west["tpdb_id"] == "scene-1"

        coded = settings.download_dir / "SSIS-001"
        write_sidecar(coded, {"tpdb_id": "scene-2"})
        both = await mgr.public(_row(str(coded)))
        assert both["kind"] == "western"
        assert both["tpdb_id"] == "scene-2"

        empty = settings.download_dir / "empty-side"
        write_sidecar(empty, {"kind": "western", "tpdb_id": ""})
        plain = await mgr.public(_row(str(empty), code="watch folder"))
        assert plain["kind"] == ""
        assert plain["tpdb_id"] == ""

        bad = settings.download_dir / "bad"
        write_sidecar(bad, {"tpdb_id": "../secret"})
        escaped = await mgr.public(_row(str(bad), code="notes"))
        assert escaped["kind"] == ""
        assert escaped["tpdb_id"] == ""

    asyncio.run(run())


def test_queue_title_links_keep_the_old_actions():
    script = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "data-queue-code" in script
    assert "data-queue-western" in script
    assert "openCodeDetail" in script
    assert "openWesternById" in script
    for act in ("pause", "resume", "cancel", "rescrape", "delete"):
        assert f'data-act="{act}"' in script


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


def test_watch_western_parks_a_tie_and_keeps_it_after_restart(tmp_path, monkeypatch):
    calls = []

    async def fake_names(settings, filename):
        calls.append(filename)
        return None, [{
            "id": "s1",
            "kind": "scene",
            "title": "One",
            "site": "Site",
            "date": "2024-01-02",
            "performers": ["Ann"],
        }]

    monkeypatch.setattr("app.western_archive.filename_candidates", fake_names)
    settings = _settings(tmp_path)
    video = settings.download_dir / "Brazzers.24.01.02.Ann.Example.mp4"
    video.write_bytes(b"x" * 80)

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        await mgr.watch_western()
        assert video.is_file()
        stored = await db.western_pending(str(video))
        assert stored["job_id"] == ""
        assert stored["candidates"][0]["id"] == "s1"
        assert stored["candidates"][0]["performers"] == ["Ann"]
        await mgr.watch_western()
        restarted = JobManager(settings, Database(settings), object())
        await restarted.watch_western()
        assert calls == ["Brazzers.24.01.02.Ann.Example.mp4"]

    asyncio.run(run())


def test_watch_western_still_archives_a_unique_filename(tmp_path, monkeypatch):
    archived = []

    async def fake_names(settings, filename):
        return {
            "id": "s9",
            "kind": "scene",
            "title": "Ann Example",
            "site": "Brazzers",
            "date": "2024-01-02",
            "performers": ["Ann"],
        }, []

    async def fake_job(settings, job, info, found=None):
        archived.append(info)
        return {"path": "Brazzers/ann"}

    monkeypatch.setattr("app.western_archive.filename_candidates", fake_names)
    monkeypatch.setattr("app.western_archive.scrape_western_job", fake_job)
    settings = _settings(tmp_path)
    video = settings.download_dir / "Brazzers.24.01.02.Ann.Example.mp4"
    video.write_bytes(b"x" * 80)

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        await mgr.watch_western()
        assert archived[0]["tpdb_id"] == "s9"
        assert await db.western_pending(str(video)) is None

    asyncio.run(run())


def test_queue_western_without_id_stops_asking_tpdb(tmp_path, monkeypatch):
    from app.western_archive import write_sidecar

    calls = []

    async def fake_names(settings, filename):
        calls.append(filename)
        return None, []

    monkeypatch.setattr("app.western_archive.filename_candidates", fake_names)
    settings = _settings(tmp_path)
    dest = settings.download_dir / "western" / "loose"
    dest.mkdir(parents=True)
    video = dest / "Brazzers.24.01.02.Ann.Example.mp4"
    video.write_bytes(b"x" * 80)
    write_sidecar(dest, {"kind": "western", "tpdb_id": "", "title": "Ann"})

    async def run():
        import aiosqlite

        db = Database(settings)
        await db.init()
        await db.insert_job(_row(str(dest), code="brazzers-ann", title="Ann Example"))
        mgr = JobManager(settings, db, object())
        out = await mgr.maybe_scrape(await db.get_job("job1"))
        assert out["scrape_status"] == "error"
        assert out["scrape_error"] == "待确认"
        assert video.is_file()
        stored = await db.western_pending(str(dest))
        assert stored["job_id"] == "job1"
        assert stored["candidates"] == []
        first = len(calls)
        assert first >= 1
        async with aiosqlite.connect(db.path) as conn:
            await conn.execute(
                "UPDATE downloads SET updated_at = ? WHERE id = ?",
                (time.time() - 1000, "job1"),
            )
            await conn.commit()
        again = await mgr.maybe_scrape(await db.get_job("job1"))
        assert again["scrape_error"] == "待确认"
        assert len(calls) == first

    asyncio.run(run())


def test_confirm_pending_writes_only_the_chosen_candidate(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.library import Library
    from app.models import WesternConfirm
    from app.routers.western import confirm_pending

    async def fake_meta(settings, info):
        return {
            "title": info.get("title") or "Ann",
            "release_date": info.get("date") or "2024-01-02",
            "studio": info.get("site") or "Brazzers",
            "actors": info.get("performers") or [],
            "cover": "https://cdn.theporndb.net/p.jpg",
            "uniqueid": info.get("tpdb_id") or "",
            "uniqueid_type": "tpdb",
            "genres": [],
            "plot": "",
            "runtime": "20",
            "url": "",
            "code": "",
        }

    async def fake_cover(settings, url, referer=None):
        return b"poster-bytes"

    monkeypatch.setattr("app.western_archive.western_metadata", fake_meta)
    monkeypatch.setattr("app.western_archive.fetch_cover_bytes", fake_cover)
    settings = _settings(tmp_path)
    video = settings.download_dir / "Brazzers.24.01.02.Ann.Example.mp4"
    video.write_bytes(b"x" * 80)
    candidates = [
        {
            "id": "keep",
            "kind": "scene",
            "title": "Ann Example",
            "site": "Brazzers",
            "date": "2024-01-02",
            "performers": ["Ann"],
        },
        {
            "id": "drop",
            "kind": "scene",
            "title": "Other",
            "site": "Other",
            "date": "2024-01-02",
            "performers": ["Bea"],
        },
    ]

    async def run():
        db = Database(settings)
        await db.init()
        await db.save_western_pending(str(video), video.name, "", candidates)
        mgr = JobManager(settings, db, object(), library=Library(settings, db))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(jobs=mgr)))
        with pytest.raises(ValueError, match="没有这条候选"):
            await mgr.confirm_western_pending(path=str(video), tpdb_id="drop", kind="movie")
        body = await confirm_pending(
            request,
            WesternConfirm(path=str(video), tpdb_id="keep", kind="scene"),
        )
        assert not video.exists()
        assert await db.western_pending(str(video)) is None
        folder = next(settings.western_root.iterdir())
        nfo = next(folder.glob("*.nfo")).read_text(encoding="utf-8")
        poster = next(folder.glob("*-poster.jpg"))
        assert "keep" in nfo
        assert "drop" not in nfo
        assert poster.read_bytes() == b"poster-bytes"
        assert poster.name.endswith("-poster.jpg")
        assert "keep" in await db.western_by_ids(["keep"])
        assert body["item"]["entries"]
        await db.save_western_pending(str(video), video.name, "", candidates)
        with pytest.raises(HTTPException) as caught:
            await confirm_pending(
                request,
                WesternConfirm(path=str(video), tpdb_id="keep", kind="scene"),
            )
        assert caught.value.status_code == 400
        assert "不在下载目录" in str(caught.value.detail)

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

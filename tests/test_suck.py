import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.batch import enqueue_batch
from app.config import Settings
from app.db import Database
from app.follow import check_sub, new_subscription
from app.library import attach_library, attach_western, remove_archived
from app.routers import downloads, library_page
from app.suck import parse_suck


def _settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "west"),
        notify_channel="",
    )
    settings.ensure_dirs()
    (tmp_path / "west").mkdir(exist_ok=True)
    return settings


def test_parse_suck_keeps_jav_code_and_western_id():
    assert parse_suck("jav", "ssis001") == ("jav", "SSIS-001")
    assert parse_suck("western", "abc") == ("western", "abc")
    with pytest.raises(ValueError):
        parse_suck("other", "SSIS-001")
    with pytest.raises(ValueError):
        parse_suck("jav", "not a code")
    with pytest.raises(ValueError):
        parse_suck("western", "../abc")


def test_attach_marks_suck_separately_from_library():
    jav = attach_library(
        [{"code": "ssis001"}, {"code": "IPX-001"}],
        {},
        {"SSIS-001"},
    )
    assert jav[0]["suck"] is True
    assert jav[0]["library"]["present"] is False
    assert jav[1]["suck"] is False
    western = attach_western([{"id": "abc"}, {"id": "nope"}], {}, {"abc"})
    assert western[0]["suck"] is True
    assert western[1]["suck"] is False


def test_remove_archived_deletes_one_work_and_stays_inside_root(tmp_path):
    media = tmp_path / "media"
    code = media / "202102" / "SSIS-001"
    code.mkdir(parents=True)
    (code / "SSIS-001.mp4").write_bytes(b"v")
    (code / "poster.jpg").write_bytes(b"p")
    keep = media / "202102" / "IPX-001"
    keep.mkdir()
    (keep / "IPX-001.mp4").write_bytes(b"v")
    assert remove_archived(media, "202102/SSIS-001", "jav") is True
    assert not code.exists()
    assert keep.is_dir()
    assert remove_archived(media, "202102/IPX-001", "jav") is True
    assert not (media / "202102").exists()

    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "x").write_text("no", encoding="utf-8")
    assert remove_archived(media, "../secret", "jav") is False
    assert (secret / "x").exists()
    assert remove_archived(media, "202102/../../secret", "jav") is False

    west = tmp_path / "west"
    studio = west / "Studio"
    studio.mkdir(parents=True)
    (studio / "clip.mp4").write_bytes(b"v")
    (studio / "clip.nfo").write_text("n", encoding="utf-8")
    (studio / "clip-poster.jpg").write_bytes(b"p")
    (studio / "other.mp4").write_bytes(b"o")
    assert remove_archived(west, "Studio/clip.mp4", "western") is True
    assert not (studio / "clip.mp4").exists()
    assert not (studio / "clip.nfo").exists()
    assert not (studio / "clip-poster.jpg").exists()
    assert (studio / "other.mp4").exists()
    assert remove_archived(west, "Studio/other.mp4", "western") is True
    assert not studio.exists()


def test_mark_removes_files_and_blocks_download(tmp_path):
    settings = _settings(tmp_path)
    folder = settings.media_dir / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "SSIS-001.mp4").write_bytes(b"v")
    (folder / "SSIS-001.nfo").write_text("<movie><title>Nope</title></movie>", encoding="utf-8")

    class Jobs:
        def __init__(self):
            self.called = False

        async def enqueue(self, *args, **kwargs):
            self.called = True
            return {"id": "job"}

        async def prepare_files(self, *args, **kwargs):
            self.called = True
            return {"mode": "direct", "job": {"id": "job"}}

        async def cancel_files(self, token):
            self.called = True

    jobs = Jobs()
    app = FastAPI()
    app.include_router(library_page.router)
    app.include_router(downloads.router)

    async def run():
        db = Database(settings)
        await db.init()
        await db.upsert_library({
            "code": "SSIS-001",
            "month": "202102",
            "path": "202102/SSIS-001",
            "has_video": 1,
            "title": "Nope",
        })
        app.state.db = db
        app.state.settings = settings
        app.state.jobs = jobs
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            marked = await client.post("/api/suck", json={
                "kind": "jav",
                "key": "ssis001",
                "remove": True,
            })
            assert marked.status_code == 200
            body = marked.json()["item"]
            assert body["key"] == "SSIS-001"
            assert body["title"] == "Nope"
            assert body["removed"] is True
            assert not folder.exists()
            assert await db.get_library("SSIS-001") is None
            assert await db.is_suck("jav", "SSIS-001") is True
            page = await client.get("/api/library")
            assert page.json()["suck"][0]["key"] == "SSIS-001"
            blocked = await client.post("/api/downloads/files", json={
                "code": "SSIS-001",
                "info_hash": "a" * 40,
                "title": "Nope",
            })
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == "已标 suck，不会再下载"
            direct = await client.post("/api/downloads", json={
                "code": "SSIS-001",
                "info_hash": "b" * 40,
                "title": "Nope",
            })
            assert direct.status_code == 409
            cleared = await client.request("DELETE", "/api/suck", json={"kind": "jav", "key": "SSIS-001"})
            assert cleared.status_code == 200
            missing = await client.request("DELETE", "/api/suck", json={"kind": "jav", "key": "SSIS-001"})
            assert missing.status_code == 404
        return jobs.called

    assert asyncio.run(run()) is False


def test_batch_skips_suck(tmp_path):
    settings = _settings(tmp_path)

    class Jobs:
        def __init__(self, db):
            self.db = db
            self.seen = []

        async def enqueue_filtered(self, code, info_hash, title):
            self.seen.append(code)
            return {"code": code, "info_hash": info_hash, "title": title}

    async def run():
        db = Database(settings)
        await db.init()
        await db.mark_suck("jav", "SSIS-001", "Nope")
        jobs = Jobs(db)
        result = await enqueue_batch(jobs, [
            {"code": "ssis001", "info_hash": "a" * 40, "title": "Nope"},
            {"code": "IPX-001", "info_hash": "b" * 40, "title": "Keep"},
        ])
        return result, jobs.seen

    result, seen = asyncio.run(run())
    assert seen == ["IPX-001"]
    assert result["skipped"] == [{"code": "SSIS-001", "reason": "已标 suck"}]
    assert result["queued"][0]["code"] == "IPX-001"


def test_follow_treats_suck_as_already_handled(tmp_path, monkeypatch):
    async def fake_notice(settings, title, body):
        raise AssertionError("suck should stay quiet")

    async def fake_list(settings, sub, known=None, pending=None):
        return [
            {"code": "SSIS-001", "title": "Nope", "western": None},
            {"code": "abc", "title": "West", "western": {"id": "abc", "title": "West"}},
        ]

    monkeypatch.setattr("app.follow.send_notice", fake_notice)
    monkeypatch.setattr("app.follow.list_works", fake_list)
    settings = _settings(tmp_path)

    class Jobs:
        def __init__(self, db):
            self.settings = settings
            self.db = db
            self.library = None
            self.calls = []

        async def enqueue(self, code, info_hash, title, dest_rel=None):
            self.calls.append(code)
            return {"id": "job", "code": code, "dest": str(settings.download_dir)}

    async def run():
        db = Database(settings)
        await db.init()
        await db.mark_suck("jav", "SSIS-001", "Nope")
        await db.mark_suck("western", "abc", "West")
        row = new_subscription(
            "actress", "葵", "https://www.javbus.com/star/2xi",
            auto=True, want_uc=False, want_c=False, max_gb=0,
        )
        row["last_check"] = 1
        await db.add_subscription(row)
        saved = await db.get_subscription(row["id"])
        saved["last_check"] = 1
        jobs = Jobs(db)
        await check_sub(jobs, saved)
        seen = await db.seen_codes(row["id"])
        hits = await db.list_hits()
        return seen, hits, jobs.calls

    seen, hits, calls = asyncio.run(run())
    assert seen == {"SSIS-001", "abc"}
    assert hits == []
    assert calls == []

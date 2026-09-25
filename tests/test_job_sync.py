import asyncio
import time

import pytest

from app.config import Settings
from app.db import Database
from app.downloader.aria2 import Aria2, Aria2Error
from app.downloader.jobs import JobManager


def _settings(tmp_path, **kwargs):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=False,
        **kwargs,
    )
    settings.ensure_dirs()
    return settings


def _job(dest: str, **overrides) -> dict:
    now = time.time()
    row = {
        "id": "job1",
        "code": "SSIS-001",
        "info_hash": "a" * 40,
        "title": "t",
        "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
        "gid": "meta",
        "status": "downloading",
        "dest": dest,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "cleaned": 0,
        "backend": "aria2",
    }
    row.update(overrides)
    return row


class ScriptedAria2(Aria2):
    def __init__(self, table):
        super().__init__(Settings())
        self.table = table
        self.removed: list[str] = []
        self.paused: list[str] = []
        self.fail_remove = False

    async def tell(self, gid: str) -> dict:
        if gid not in self.table:
            raise Aria2Error(f"GID {gid} is not found")
        return dict(self.table[gid])

    async def pause(self, gid: str) -> None:
        self.paused.append(gid)

    async def remove(self, gid: str) -> None:
        if self.fail_remove:
            raise Aria2Error("连接失败")
        self.removed.append(gid)

    async def add_magnet(self, magnet: str, dest: str) -> str:
        return "ab" * 8


class ListXunlei:
    def __init__(self, index=None, truncated=False):
        self.index = index or {}
        self.truncated = truncated
        self.calls = 0

    async def list_download_tasks(self):
        self.calls += 1
        return self.index, self.truncated

    async def add_magnet(self, magnet: str, dest: str) -> str:
        return "T9"


def _running(task_id: str, done="10", total="100") -> dict:
    return {
        "id": task_id,
        "phase": "PHASE_TYPE_RUNNING",
        "file_size": total,
        "params": {"checked_size": done, "speed": "5"},
    }


def test_resolve_follows_metadata_gid():
    aria = ScriptedAria2({
        "meta": {
            "status": "complete",
            "followedBy": ["content"],
            "totalLength": "20",
            "completedLength": "20",
        },
        "content": {
            "status": "active",
            "totalLength": "1000",
            "completedLength": "100",
            "downloadSpeed": "8",
        },
    })

    status = asyncio.run(aria.resolve("meta"))

    assert status["status"] == "active"
    assert status["stored"] == "content"
    assert status["totalLength"] == "1000"
    assert status["completedLength"] == "100"


def test_resolve_waits_for_every_followed_gid():
    aria = ScriptedAria2({
        "meta": {"status": "complete", "followedBy": ["one", "two"], "totalLength": "1"},
        "one": {"status": "complete", "totalLength": "10", "completedLength": "10"},
        "two": {"status": "active", "totalLength": "10", "completedLength": "1"},
    })

    status = asyncio.run(aria.resolve("meta"))

    assert status["status"] == "active"
    assert status["stored"] == "one,two"


def test_remove_missing_gid_is_stopped():
    aria = Aria2(Settings())

    async def call(method, params=None):
        raise Aria2Error("GID abc is not found")

    aria.call = call
    asyncio.run(aria.remove("abc"))


def test_remove_connection_error_is_raised():
    aria = Aria2(Settings())

    async def call(method, params=None):
        raise Aria2Error("连接失败")

    aria.call = call
    with pytest.raises(Aria2Error, match="连接失败"):
        asyncio.run(aria.remove("abc"))


def test_sync_does_not_clean_until_content_finishes(tmp_path):
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir()
    control = dest / "movie.mp4.aria2"
    control.write_bytes(b"ctl")
    aria = ScriptedAria2({
        "meta": {"status": "complete", "followedBy": ["content"], "totalLength": "20", "completedLength": "20"},
        "content": {"status": "active", "totalLength": "1000", "completedLength": "10", "downloadSpeed": "4"},
    })

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(dest), status="complete", cleaned=1))
        mgr = JobManager(settings, db, aria, ListXunlei())
        out = await mgr.sync_one(await db.get_job("job1"))
        assert out["status"] == "downloading"
        assert out["gid"] == "content"
        assert out["cleaned"] == 0
        assert control.exists()
        aria.table["content"] = {
            "status": "complete",
            "totalLength": "1000",
            "completedLength": "1000",
        }
        done = await mgr.sync_one(await db.get_job("job1"))
        assert done["status"] == "complete"
        assert done["cleaned"] == 1
        assert not control.exists()

    asyncio.run(run())


def test_cancel_keeps_the_job_when_remove_fails(tmp_path):
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir()
    aria = ScriptedAria2({
        "meta": {"status": "complete", "followedBy": ["content"], "totalLength": "20", "completedLength": "20"},
        "content": {"status": "active", "totalLength": "1000", "completedLength": "10"},
    })
    aria.fail_remove = True

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(dest)))
        mgr = JobManager(settings, db, aria, ListXunlei())
        with pytest.raises(Aria2Error, match="连接失败"):
            await mgr.cancel("job1")
        stored = await db.get_job("job1")
        assert stored["status"] == "downloading"
        assert stored["gid"] == "content"
        assert aria.removed == []

    asyncio.run(run())


def test_cancel_removes_the_content_gid(tmp_path):
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir()
    aria = ScriptedAria2({
        "meta": {"status": "complete", "followedBy": ["content"], "totalLength": "20", "completedLength": "20"},
        "content": {"status": "active", "totalLength": "1000", "completedLength": "10"},
    })

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(dest)))
        mgr = JobManager(settings, db, aria, ListXunlei())
        await mgr.cancel("job1")
        stored = await db.get_job("job1")
        assert stored["status"] == "cancelled"
        assert aria.removed == ["content"]

    asyncio.run(run())


def test_pause_targets_the_content_gid(tmp_path):
    settings = _settings(tmp_path)
    dest = settings.download_dir / "SSIS-001"
    dest.mkdir()
    aria = ScriptedAria2({
        "meta": {"status": "complete", "followedBy": ["content"], "totalLength": "20", "completedLength": "20"},
        "content": {"status": "active", "totalLength": "1000", "completedLength": "10"},
    })

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(dest)))
        mgr = JobManager(settings, db, aria, ListXunlei())
        await mgr.pause("job1")
        stored = await db.get_job("job1")
        assert stored["status"] == "paused"
        assert aria.paused == ["content"]

    asyncio.run(run())


def test_xunlei_complete_missing_stays_complete(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({})

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(
            str(settings.download_dir),
            gid="T1",
            backend="xunlei",
            status="complete",
            cleaned=1,
        ))
        mgr = JobManager(settings, db, ScriptedAria2({}), panel)
        items = await mgr.list_public()
        assert items[0]["status"] == "complete"
        assert items[0]["error"] is None
        assert panel.calls == 0
        stored = await db.get_job("job1")
        assert stored["status"] == "complete"

    asyncio.run(run())


def test_xunlei_deleted_active_task_becomes_error_once_the_list_is_complete(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({}, truncated=False)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(settings.download_dir), gid="T1", backend="xunlei"))
        mgr = JobManager(settings, db, ScriptedAria2({}), panel)
        out = await mgr.sync_one(await db.get_job("job1"))
        assert out["status"] == "error"
        assert "不存在" in (out["error"] or "")

    asyncio.run(run())


def test_xunlei_truncated_list_does_not_flip_unseen_tasks(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({}, truncated=True)

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(settings.download_dir), gid="T1", backend="xunlei"))
        mgr = JobManager(settings, db, ScriptedAria2({}), panel)
        out = await mgr.sync_one(await db.get_job("job1"))
        assert out["status"] == "downloading"
        assert out["error"] is None

    asyncio.run(run())


def test_xunlei_list_is_fetched_once_for_the_whole_queue(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({
        "T1": _running("T1", "40", "100"),
        "T2": _running("T2", "10", "200"),
    })

    async def run():
        db = Database(settings)
        await db.init()
        first = _job(str(settings.download_dir), id="job1", gid="T1", backend="xunlei")
        second = _job(str(settings.download_dir), id="job2", gid="T2", backend="xunlei", code="SSIS-002")
        await db.insert_job(first)
        await db.insert_job(second)
        mgr = JobManager(settings, db, ScriptedAria2({}), panel)
        items = await mgr.list_public()
        assert panel.calls == 1
        by_id = {item["id"]: item for item in items}
        assert by_id["job1"]["status"] == "downloading"
        assert by_id["job1"]["progress"] == 40.0
        assert by_id["job2"]["progress"] == 5.0

    asyncio.run(run())


def test_old_aria2_job_is_not_sent_to_xunlei(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({})
    aria = ScriptedAria2({
        "ab" * 8: {"status": "active", "totalLength": "100", "completedLength": "25", "downloadSpeed": "1"},
    })

    async def run():
        db = Database(settings)
        await db.init()
        await db.insert_job(_job(str(settings.download_dir), gid="ab" * 8, backend=""))
        mgr = JobManager(settings, db, aria, panel)
        out = await mgr.sync_one(await db.get_job("job1"))
        assert out["status"] == "downloading"
        assert panel.calls == 0

    asyncio.run(run())


def test_enqueue_records_the_downloader_in_use(tmp_path):
    settings = _settings(tmp_path, downloader="xunlei")
    panel = ListXunlei({"T9": _running("T9")})

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, ScriptedAria2({}), panel)
        created = await mgr.enqueue("SSIS-001", "b" * 40, "title")
        stored = await db.get_job(created["id"])
        assert stored["backend"] == "xunlei"
        assert stored["gid"] == "T9"

    asyncio.run(run())

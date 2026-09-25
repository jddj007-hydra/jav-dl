import asyncio

from app.config import Settings
from app.db import Database
from app.downloader.aria2 import Aria2, Aria2Error
from app.downloader.jobs import JobManager
from app.pickfiles import (
    aria_content_files,
    default_selected,
    flatten_xunlei_files,
    format_select_file,
    format_sub_file_index,
)


def test_default_selection_drops_ads_samples_and_tiny_extras():
    files = [
        {"name": "SSIS-001.mkv", "size": 2_000_000_000},
        {"name": "SSIS-001-sample.mp4", "size": 80_000_000},
        {"name": "manko.fun.url", "size": 80},
        {"name": "cover.jpg", "size": 400_000},
        {"name": "extra.rar", "path": "extra.rar", "size": 900_000_000},
        {"name": "note.txt", "size": 20},
    ]
    picked = [files[i]["name"] for i, keep in enumerate(default_selected(files)) if keep]
    assert picked == ["SSIS-001.mkv", "extra.rar"]


def test_default_selection_keeps_the_largest_when_everything_looks_like_junk():
    files = [
        {"name": "sample-a.mp4", "size": 10},
        {"name": "preview.mkv", "size": 50},
    ]
    assert default_selected(files) == [False, True]


def test_xunlei_index_and_nested_names():
    resource = {
        "name": "pack",
        "file_count": 3,
        "dir": {"resources": [
            {"name": "SSIS-001.mkv", "file_index": 0, "file_size": 100},
            {"name": "ads", "is_dir": True, "dir": {"resources": [
                {"name": "manko.fun.jpg", "file_index": 2, "file_size": 10},
            ]}},
        ]},
    }
    files = flatten_xunlei_files(resource)
    assert [item["index"] for item in files] == [0, 2]
    assert format_sub_file_index([0], [0, 2]) == "0"
    assert format_sub_file_index([0, 2], [0, 2]) == "0,2"
    assert format_sub_file_index([0, 1, 2], [0, 1, 2]) == "0-2"
    assert format_sub_file_index([0], [0]) == "--1,"
    assert format_select_file([1, 3, 3]) == "1,3"


def test_metadata_file_is_not_a_content_list():
    assert aria_content_files({
        "files": [{"index": "1", "path": "/dl/[METADATA]abc.torrent", "length": "0"}],
    }) is None


def _settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        downloader="aria2",
    )
    settings.ensure_dirs()
    return settings


class _Aria(Aria2):
    def __init__(self, table):
        super().__init__(Settings())
        self.table = table
        self.added = 0
        self.paused = []
        self.resumed = []
        self.removed = []
        self.options = []

    async def add_magnet(self, magnet, dest):
        self.added += 1
        return "meta"

    async def tell(self, gid):
        if gid not in self.table:
            raise Aria2Error(f"GID {gid} is not found")
        return dict(self.table[gid])

    async def pause(self, gid):
        self.paused.append(gid)

    async def resume(self, gid):
        self.resumed.append(gid)

    async def remove(self, gid):
        self.removed.append(gid)

    async def change_option(self, gid, options):
        self.options.append((gid, options))


class _Panel:
    def __init__(self, files):
        self.files = files
        self.added = []

    async def list_magnet_files(self, magnet):
        return list(self.files)

    async def add_magnet(self, magnet, dest, sub_file_index=None):
        self.added.append(sub_file_index)
        return "T1"

    async def list_download_tasks(self):
        return {}, False


def test_one_file_enqueues_without_a_picker(tmp_path):
    settings = _settings(tmp_path)
    aria = _Aria({"meta": {
        "status": "active",
        "files": [{"index": "1", "path": str(settings.download_dir / "SSIS-001.mkv"), "length": "100"}],
    }})

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))
        result = await mgr.prepare_files("SSIS-001", "a" * 40, "SSIS-001.mkv")
        stored = await db.list_jobs()
        return result, stored

    result, stored = asyncio.run(run())
    assert result["mode"] == "direct"
    assert result["job"]["code"] == "SSIS-001"
    assert aria.added == 1
    assert aria.paused == ["meta"]
    assert aria.resumed == ["meta"]
    assert aria.options == []
    assert len(stored) == 1
    assert stored[0]["gid"] == "meta"


def test_multi_file_waits_for_a_choice_then_uses_select_file(tmp_path):
    settings = _settings(tmp_path)
    video = str(settings.download_dir / "SSIS-001.mkv")
    sample = str(settings.download_dir / "sample.mp4")
    aria = _Aria({
        "meta": {
            "status": "complete",
            "followedBy": ["content"],
            "files": [{"index": "1", "path": "/dl/[METADATA]abc.torrent", "length": "20"}],
        },
        "content": {
            "status": "active",
            "files": [
                {"index": "1", "path": video, "length": "2000"},
                {"index": "2", "path": sample, "length": "30"},
            ],
        },
    })

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))
        preview = await mgr.prepare_files("SSIS-001", "b" * 40, "pack")
        assert await db.list_jobs() == []
        chosen = [item["index"] for item in preview["files"] if item["selected"]]
        job = await mgr.confirm_files(preview["token"], chosen, "b" * 40)
        return preview, chosen, job, await db.list_jobs()

    preview, chosen, job, stored = asyncio.run(run())
    assert preview["mode"] == "choose"
    assert chosen == [1]
    assert preview["files"][1]["name"] == "sample.mp4"
    assert preview["files"][1]["selected"] is False
    assert aria.paused == ["content"]
    assert aria.options == [("content", {"select-file": "1"})]
    assert aria.resumed == ["content"]
    assert job["code"] == "SSIS-001"
    assert stored[0]["gid"] == "content"
    assert len(stored) == 1


def test_enqueue_filtered_drops_the_sample_without_asking(tmp_path):
    settings = _settings(tmp_path)
    video = str(settings.download_dir / "SSIS-001.mkv")
    sample = str(settings.download_dir / "sample.mp4")
    aria = _Aria({
        "meta": {
            "status": "complete",
            "followedBy": ["content"],
            "files": [{"index": "1", "path": "/dl/[METADATA]abc.torrent", "length": "20"}],
        },
        "content": {
            "status": "active",
            "files": [
                {"index": "1", "path": video, "length": "2000"},
                {"index": "2", "path": sample, "length": "30"},
            ],
        },
    })

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))
        job = await mgr.enqueue_filtered("SSIS-001", "b" * 40, "pack")
        return job, await db.list_jobs()

    job, stored = asyncio.run(run())
    assert aria.options == [("content", {"select-file": "1"})]
    assert job["code"] == "SSIS-001"
    assert stored[0]["gid"] == "content"


def test_enqueue_filtered_queues_whole_when_the_file_list_is_slow(tmp_path):
    from app.downloader.aria2 import Aria2MetadataTimeout

    settings = _settings(tmp_path)
    aria = _Aria({})

    async def slow_inspect(magnet, dest):
        raise Aria2MetadataTimeout("暂时读不到种子里的文件")

    aria.inspect_files = slow_inspect

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))
        job = await mgr.enqueue_filtered("SSIS-001", "b" * 40, "cold")
        return job, await db.list_jobs()

    job, stored = asyncio.run(run())
    assert job["code"] == "SSIS-001"
    assert aria.added == 1
    assert stored[0]["gid"] == "meta"


def test_failed_confirm_keeps_the_pick_so_the_torrent_is_removed(tmp_path):
    settings = _settings(tmp_path)
    aria = _Aria({"meta": {
        "status": "active",
        "files": [
            {"index": "1", "path": "/dl/a.mkv", "length": "10"},
            {"index": "2", "path": "/dl/b.mkv", "length": "10"},
        ],
    }})

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))

        async def broken_insert(job):
            raise RuntimeError("disk full")

        db.insert_job = broken_insert
        try:
            await mgr.enqueue_filtered("SSIS-001", "c" * 40, "two")
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the insert to fail")
        return mgr._picks

    picks = asyncio.run(run())
    assert picks == {}
    assert aria.resumed == ["meta"]
    assert aria.removed == ["meta"]


def test_cancel_and_timeout_remove_the_paused_torrent(tmp_path):
    settings = _settings(tmp_path)
    aria = _Aria({"meta": {
        "status": "active",
        "files": [
            {"index": "1", "path": "/dl/a.mkv", "length": "10"},
            {"index": "2", "path": "/dl/b.mkv", "length": "10"},
        ],
    }})

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, aria, _Panel([]))
        preview = await mgr.prepare_files("SSIS-001", "c" * 40, "two")
        await mgr.cancel_files(preview["token"])
        assert preview["token"] not in mgr._picks
        again = await mgr.prepare_files("SSIS-002", "d" * 40, "two")
        mgr._picks[again["token"]]["at"] -= 10 * 60 + 1
        await mgr._expire_picks()
        return again["token"] not in mgr._picks

    assert asyncio.run(run()) is True
    assert aria.removed.count("meta") == 2
    assert aria.resumed == []


def test_xunlei_choice_sends_sub_file_index_and_one_file_does_not(tmp_path):
    settings = _settings(tmp_path)
    settings.downloader = "xunlei"
    panel = _Panel([
        {"index": 0, "name": "SSIS-001.mkv", "path": "SSIS-001.mkv", "size": 2000},
        {"index": 1, "name": "manko.fun.url", "path": "manko.fun.url", "size": 40},
        {"index": 3, "name": "SSIS-001-C.mkv", "path": "SSIS-001-C.mkv", "size": 2100},
    ])

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, _Aria({}), panel)
        preview = await mgr.prepare_files("SSIS-001", "e" * 40, "pack")
        chosen = [item["index"] for item in preview["files"] if item["selected"]]
        await mgr.confirm_files(preview["token"], chosen, "e" * 40)
        single = _Panel([{"index": 0, "name": "only.mkv", "path": "only.mkv", "size": 100}])
        mgr.xunlei = single
        direct = await mgr.prepare_files("SSIS-002", "f" * 40, "only")
        return preview["mode"], chosen, panel.added, direct["mode"], single.added

    mode, chosen, added, direct_mode, single_added = asyncio.run(run())
    assert mode == "choose"
    assert chosen == [0, 3]
    assert added == ["0,3"]
    assert direct_mode == "direct"
    assert single_added == [None]

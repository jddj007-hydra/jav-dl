import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.downloader.jobs import JobManager
from app.routers.downloads import iter_download_events


def test_queue_page_uses_sse_instead_of_the_two_second_poll():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "new EventSource(\"/api/downloads/events\")" in script
    assert "visibilitychange" in script
    assert "30000" in script
    assert "if (!views.queue.hidden) refreshQueue();" not in script


def test_fanout_keeps_only_the_latest_snapshot(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object())
        queue = mgr.subscribe()
        mgr._fanout({"items": [{"code": "SSIS-001"}], "follow_unread": 0})
        mgr._fanout({"items": [{"code": "SSIS-002"}], "follow_unread": 1})
        payload = queue.get_nowait()
        mgr.unsubscribe(queue)
        return payload, queue.empty()

    payload, empty = asyncio.run(run())
    assert payload["items"] == [{"code": "SSIS-002"}]
    assert payload["follow_unread"] == 1
    assert empty is True


def test_event_stream_sends_the_current_list_then_updates():
    class Jobs:
        def __init__(self):
            self.queues = []
            self.items = [{"code": "SSIS-001", "status": "downloading"}]

        def subscribe(self):
            queue = asyncio.Queue(maxsize=1)
            self.queues.append(queue)
            return queue

        def unsubscribe(self, queue):
            if queue in self.queues:
                self.queues.remove(queue)

        async def list_public(self):
            return list(self.items)

    jobs = Jobs()
    closed = False

    async def disconnected():
        return closed

    async def run():
        stream = iter_download_events(jobs, None, disconnected)
        first = json.loads((await stream.__anext__())[6:].strip())
        jobs.items = [{"code": "SSIS-001", "status": "complete"}]
        jobs.queues[0].put_nowait({"items": list(jobs.items), "follow_unread": 2})
        second = json.loads((await stream.__anext__())[6:].strip())
        await stream.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first["items"][0]["status"] == "downloading"
    assert second["items"][0]["status"] == "complete"
    assert second["follow_unread"] == 2
    assert jobs.queues == []

import asyncio
from types import SimpleNamespace

from app.downloader.xunlei import Xunlei, file_index_from_list, map_phase

DEVICE = "device_id#abc"


class FakePanel(Xunlei):
    def __init__(self):
        super().__init__(SimpleNamespace(
            xunlei_url="http://nas:2345",
            xunlei_username="u",
            xunlei_password="p",
            xunlei_device_name="群晖-xunlei",
        ))
        self.calls: list[tuple[str, str]] = []
        self.created: dict | None = None

    async def token(self) -> str:
        return "tok"

    async def _json(self, method, path, *, token=None, json_body=None, timeout=25.0):
        self.calls.append((method, path))
        if path.startswith("drive/v1/tasks?type=user%23runner"):
            return {"tasks": [{"type": "user#runner", "name": "群晖-xunlei", "params": {"target": DEVICE}}]}
        if path.startswith("drive/v1/tasks?type=user%23download-url&space=device_id%23abc"):
            tasks = []
            if self.created:
                tasks.append({
                    "id": "T1",
                    "type": "user#download-url",
                    "phase": "PHASE_TYPE_RUNNING",
                    "file_size": "100",
                    "params": {"url": self.created["params"]["url"], "checked_size": "40", "speed": "10"},
                })
            return {"tasks": tasks}
        if path.startswith("drive/v1/files?space=device_id%23abc"):
            return {"files": [{"id": "F1", "name": "downloads", "kind": "drive#folder"}]}
        if path == "drive/v1/resource/list":
            return {"list": {"resources": [{"name": "movie", "file_size": 100, "file_count": 1}]}}
        if path == "drive/v1/task":
            self.created = json_body
            return {"task": {"id": "T1"}}
        raise AssertionError(f"unexpected {method} {path}")


def test_file_index_single():
    listed = {"list": {"resources": [{"name": "a", "file_count": 1, "file_size": 10}]}}
    assert file_index_from_list(listed) == "--1,"


def test_file_index_multi():
    listed = {
        "list": {
            "resources": [{
                "name": "pack",
                "file_count": 3,
                "file_size": 100,
                "dir": {"resources": [
                    {"file_index": 0},
                    {"file_index": 2},
                ]},
            }]
        }
    }
    assert file_index_from_list(listed) == "0-2"


def test_map_phase_running():
    assert map_phase({"phase": "PHASE_TYPE_RUNNING", "params": {"status": '{"phase":"running"}'}}) == "active"


def test_map_phase_complete():
    assert map_phase({"phase": "PHASE_TYPE_COMPLETE", "params": {}}) == "complete"


def test_add_magnet_then_tell():
    panel = FakePanel()
    magnet = "magnet:?xt=urn:btih:" + "a" * 40

    async def run():
        gid = await panel.add_magnet(magnet, "/downloads/X")
        return gid, await panel.tell(gid)

    gid, st = asyncio.run(run())
    assert gid == "T1"
    assert panel.created["space"] == DEVICE
    assert panel.created["params"]["parent_folder_id"] == "F1"
    assert st["status"] == "active"
    assert st["completedLength"] == "40"
    assert all("type=" in p for m, p in panel.calls if p.startswith("drive/v1/tasks"))

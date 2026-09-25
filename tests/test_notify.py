import asyncio
import logging

import httpx

from app.config import Settings, save_user_config
from app.db import Database
from app.downloader.aria2 import Aria2
from app.downloader.jobs import JobManager
from app.notify import send_notice
from app.scrape import ScrapeError


class _Hold:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, status=200):
        self.status = status
        self.posts = []

    async def post(self, url, json=None, data=None, timeout=None):
        self.posts.append((url, json, data))
        response = httpx.Response(self.status, request=httpx.Request("POST", url))
        response.raise_for_status()
        return response


def test_channels_post_the_right_payload_and_hide_secrets(monkeypatch):
    client = _Client()
    monkeypatch.setattr("app.notify.site_client", lambda settings: _Hold(client))

    async def run():
        await send_notice(Settings(notify_channel=""), "下载完成", "SSIS-001")
        await send_notice(
            Settings(notify_channel="telegram", notify_telegram_token="tok", notify_telegram_chat="99"),
            "下载完成",
            "SSIS-001 t",
        )
        await send_notice(Settings(notify_channel="bark", notify_bark_url="devicekey"), "归档完成", "path")
        await send_notice(
            Settings(notify_channel="serverchan", notify_serverchan_key="SCT123"),
            "下载失败",
            "SSIS-001\n磁盘满了",
        )

    asyncio.run(run())
    assert len(client.posts) == 3
    assert client.posts[0][0] == "https://api.telegram.org/bottok/sendMessage"
    assert client.posts[0][1]["chat_id"] == "99"
    assert "SSIS-001 t" in client.posts[0][1]["text"]
    assert client.posts[1][0] == "https://api.day.app/devicekey"
    assert client.posts[1][1]["title"] == "归档完成"
    assert client.posts[2][0] == "https://sctapi.ftqq.com/SCT123.send"
    assert client.posts[2][2]["desp"] == "SSIS-001\n磁盘满了"


def test_notice_failure_is_logged_and_not_raised(monkeypatch, caplog):
    monkeypatch.setattr("app.notify.site_client", lambda settings: _Hold(_Client(status=500)))
    caplog.set_level(logging.WARNING, logger="app.notify")

    async def run():
        await send_notice(
            Settings(notify_channel="bark", notify_bark_url="https://bark.example/key"),
            "下载失败",
            "SSIS-001",
        )

    asyncio.run(run())
    assert any("通知失败" in rec.message for rec in caplog.records)


def test_blank_secret_keeps_the_saved_channel(tmp_path):
    settings = Settings(data_dir=tmp_path, download_dir=tmp_path / "dl", media_dir=tmp_path / "media")
    settings.ensure_dirs()
    saved = save_user_config(settings, {
        "notify_channel": "telegram",
        "notify_telegram_token": "tok",
        "notify_telegram_chat": "99",
    })
    assert saved.notify_telegram_token == "tok"
    pub = saved.public_dict()
    assert pub["notify_telegram_token_set"] is True
    assert "tok" not in pub.values()
    assert pub["notify_telegram_chat"] == "99"
    again = save_user_config(saved, {"notify_channel": "telegram", "notify_telegram_chat": "100"})
    assert again.notify_telegram_token == "tok"
    assert again.notify_telegram_chat == "100"
    kept = save_user_config(again, {"notify_channel": "nope"})
    assert kept.notify_channel == "telegram"
    off = save_user_config(kept, {"notify_channel": "none"})
    assert off.notify_channel == ""
    assert off.notify_telegram_token == "tok"


class _Aria(Aria2):
    def __init__(self, status):
        super().__init__(Settings())
        self.status = status

    async def tell(self, gid):
        return dict(self.status)


def _mgr(tmp_path, status, monkeypatch):
    notes = []

    async def fake(settings, title, body):
        notes.append((title, body))

    monkeypatch.setattr("app.downloader.jobs.send_notice", fake)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        scrape_enabled=False,
        notify_channel="bark",
    )
    settings.ensure_dirs()
    return settings, notes


def test_download_complete_and_failure_notify_once(tmp_path, monkeypatch):
    settings, notes = _mgr(tmp_path, None, monkeypatch)

    async def run():
        db = Database(settings)
        await db.init()
        now = 1.0
        job = {
            "id": "job1",
            "code": "SSIS-001",
            "info_hash": "a" * 40,
            "title": "title",
            "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
            "gid": "meta",
            "status": "downloading",
            "dest": str(settings.download_dir),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 0,
            "backend": "aria2",
        }
        await db.insert_job(job)
        done = _Aria({
            "gid": "meta",
            "status": "complete",
            "totalLength": "10",
            "completedLength": "10",
            "downloadSpeed": "0",
            "files": [],
        })
        mgr = JobManager(settings, db, done)
        await mgr.sync_one(dict(job))
        stored = await db.get_job("job1")
        await mgr.sync_one(stored)
        failed = dict(job)
        failed["id"] = "job2"
        failed["info_hash"] = "b" * 40
        failed["gid"] = "meta2"
        await db.insert_job(failed)
        bad = _Aria({
            "gid": "meta2",
            "status": "error",
            "totalLength": "10",
            "completedLength": "0",
            "downloadSpeed": "0",
            "errorMessage": "磁盘满了",
            "files": [],
        })
        mgr.aria2 = bad
        await mgr.sync_one(dict(failed))
        await mgr.sync_one(await db.get_job("job2"))
        return notes

    sent = asyncio.run(run())
    assert sent == [
        ("下载完成", "SSIS-001 title"),
        ("下载失败", "SSIS-001 title\n磁盘满了"),
    ]


def test_archive_success_and_failure_notify(tmp_path, monkeypatch):
    settings, notes = _mgr(tmp_path, None, monkeypatch)
    settings.scrape_enabled = True

    def boom(*args, **kwargs):
        raise ScrapeError("没有可归档的视频")

    monkeypatch.setattr("app.downloader.jobs.find_code_videos", boom)

    async def run():
        db = Database(settings)
        await db.init()
        now = 1.0
        job = {
            "id": "job1",
            "code": "SSIS-001",
            "info_hash": "a" * 40,
            "title": "title",
            "magnet": "m",
            "gid": "meta",
            "status": "complete",
            "dest": str(settings.download_dir),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 1,
            "backend": "aria2",
            "scrape_status": "",
        }
        await db.insert_job(job)
        mgr = JobManager(settings, db, _Aria({}))
        await mgr._mark_archived(dict(job), "/media/202101/SSIS-001")
        await mgr.maybe_scrape(dict(job))
        return notes

    sent = asyncio.run(run())
    assert sent[0] == ("归档完成", "SSIS-001 title\n/media/202101/SSIS-001")
    assert sent[1][0] == "归档失败"
    assert "没有可归档的视频" in sent[1][1]


def test_archive_failure_notifies_once_across_retries(tmp_path, monkeypatch):
    settings, notes = _mgr(tmp_path, None, monkeypatch)
    settings.scrape_enabled = True
    settings.scrape_settle_seconds = 0
    settings.scrape_min_mb = 0

    def boom(*args, **kwargs):
        raise ScrapeError("没有可归档的视频")

    async def watch_boom(*args, **kwargs):
        raise ScrapeError("元数据失败")

    monkeypatch.setattr("app.downloader.jobs.find_code_videos", boom)
    monkeypatch.setattr("app.downloader.jobs.scrape_job", watch_boom)
    root = settings.download_dir
    folder = root / "MIDV-002"
    folder.mkdir(parents=True)
    (folder / "a.mp4").write_bytes(b"x" * 80)

    async def run():
        db = Database(settings)
        await db.init()
        now = 1.0
        job = {
            "id": "job1",
            "code": "SSIS-001",
            "info_hash": "a" * 40,
            "title": "title",
            "magnet": "m",
            "gid": "meta",
            "status": "complete",
            "dest": str(root / "SSIS-001"),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 1,
            "backend": "aria2",
            "scrape_status": "",
        }
        await db.insert_job(job)
        mgr = JobManager(settings, db, _Aria({}))
        await mgr.maybe_scrape(dict(job))
        stored = await db.get_job("job1")
        stored["updated_at"] = 1.0
        await mgr.maybe_scrape(stored)
        await mgr.watch_downloads()
        for key in list(mgr._watch_fail):
            mgr._watch_fail[key] = 0
        await mgr.watch_downloads()
        return notes

    sent = asyncio.run(run())
    failures = [item for item in sent if item[0] == "归档失败"]
    assert len(failures) == 2
    assert failures[0][1].startswith("SSIS-001 title")
    assert failures[1][1].startswith("MIDV-002")

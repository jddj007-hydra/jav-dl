import asyncio
import time

from app.config import Settings
from app.db import Database
from app.follow import check_sub, magnet_matches, new_subscription, resolve_target
from app.sources.javbus import parse_star_links


def test_parse_star_links_and_rules():
    html = """
    <a href="/star/2xi"><img title="葵つかさ"><span>葵つかさ</span><span>有碼</span></a>
    <a href="https://www.javbus.com/star/abc">三上悠亜</a>
    """
    links = parse_star_links(html, "https://www.javbus.com")
    assert links[0]["url"].endswith("/star/2xi")
    assert links[0]["name"] == "葵つかさ"
    assert links[1]["name"] == "三上悠亜"
    sub = {"want_uc": 1, "want_c": 1, "max_gb": 8}
    assert magnet_matches({"tags": ["UC"], "size_bytes": 2 * 1000**3}, sub)
    assert magnet_matches({"tags": ["C"], "size_bytes": 2 * 1000**3}, sub)
    assert not magnet_matches({"tags": [], "size_bytes": 2 * 1000**3}, sub)
    assert not magnet_matches({"tags": ["UC"], "size_bytes": 20 * 1000**3}, sub)
    assert magnet_matches({"tags": [], "size_bytes": 20 * 1000**3}, {"want_uc": 0, "want_c": 0, "max_gb": 0})


def test_series_link_must_match_the_kind():
    async def run():
        name, url = await resolve_target(
            Settings(),
            "series",
            "",
            "https://www.javbus.com/series/abc",
        )
        assert name == "abc"
        assert url == "https://www.javbus.com/series/abc"
        try:
            await resolve_target(Settings(), "actress", "葵", "https://www.javbus.com/series/abc")
        except ValueError as exc:
            assert "女优" in str(exc)
        else:
            raise AssertionError("expected kind mismatch")

    asyncio.run(run())


def _settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        notify_channel="",
    )
    settings.ensure_dirs()
    return settings


class _Jobs:
    def __init__(self, settings, db, library=None):
        self.settings = settings
        self.db = db
        self.library = library
        self.calls = []

    async def enqueue(self, code, info_hash, title, dest_rel=None):
        self.calls.append((code, info_hash, title))
        return {"id": "job", "code": code, "dest": str(self.settings.download_dir), "info_hash": info_hash}


def test_first_check_only_remembers_and_later_check_reminds(tmp_path, monkeypatch):
    notes = []

    async def fake_notice(settings, title, body):
        notes.append((title, body))

    works = [{"code": "SSIS-001", "title": "one", "western": None}]

    async def fake_list(settings, sub):
        return list(works)

    monkeypatch.setattr("app.follow.send_notice", fake_notice)
    monkeypatch.setattr("app.follow.list_works", fake_list)
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        row = new_subscription("actress", "葵つかさ", "https://www.javbus.com/star/2xi", auto=False, want_uc=False, want_c=False, max_gb=0)
        await db.add_subscription(row)
        jobs = _Jobs(settings, db)
        first = await check_sub(jobs, row)
        works.append({"code": "SSIS-002", "title": "two", "western": None})
        saved = await db.get_subscription(row["id"])
        second = await check_sub(jobs, saved)
        hits = await db.list_hits()
        return first, second, hits, jobs.calls

    first, second, hits, calls = asyncio.run(run())
    assert first["first"] is True and first["added"] == 0
    assert second["added"] == 1
    assert hits[0]["code"] == "SSIS-002"
    assert hits[0]["status"] == "new"
    assert notes == [("追更新作", "SSIS-002 two\n来自 葵つかさ")]
    assert calls == []


def test_auto_download_uses_rules_and_skips_owned(tmp_path, monkeypatch):
    async def fake_notice(settings, title, body):
        return None

    async def fake_list(settings, sub):
        return [
            {"code": "SSIS-001", "title": "owned", "western": None},
            {"code": "SSIS-002", "title": "queued", "western": None},
            {"code": "SSIS-003", "title": "new", "western": None},
            {"code": "SSIS-004", "title": "plain", "western": None},
        ]

    async def fake_search(settings, code, pages=2):
        if code == "SSIS-004":
            return [{"info_hash": "d" * 40, "title": "SSIS-004 plain", "heat": 5, "size_bytes": 1000, "tags": []}]
        return [
            {"info_hash": "a" * 40, "title": "SSIS-003 huge UC", "heat": 9, "size_bytes": 20 * 1000**3, "tags": ["UC"]},
            {"info_hash": "b" * 40, "title": "SSIS-003 plain", "heat": 8, "size_bytes": 1000, "tags": []},
            {"info_hash": "c" * 40, "title": "SSIS-003-UC", "heat": 1, "size_bytes": 2 * 1000**3, "tags": ["UC"]},
        ]

    monkeypatch.setattr("app.follow.send_notice", fake_notice)
    monkeypatch.setattr("app.follow.list_works", fake_list)
    monkeypatch.setattr("app.follow.search_magnets", fake_search)
    settings = _settings(tmp_path)

    class Lib:
        async def get(self, code):
            if code == "SSIS-001":
                return {"has_video": 1, "code": code}
            return None

    async def run():
        db = Database(settings)
        await db.init()
        now = time.time()
        await db.insert_job({
            "id": "old",
            "code": "SSIS-002",
            "info_hash": "e" * 40,
            "title": "queued",
            "magnet": "m",
            "gid": "",
            "status": "downloading",
            "dest": str(settings.download_dir),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 0,
            "backend": "aria2",
        })
        row = new_subscription(
            "actress", "葵", "https://www.javbus.com/star/2xi",
            auto=True, want_uc=True, want_c=False, max_gb=8,
        )
        row["last_check"] = now
        await db.add_subscription(row)
        saved = await db.get_subscription(row["id"])
        saved["last_check"] = now
        jobs = _Jobs(settings, db, Lib())
        await check_sub(jobs, saved)
        hits = {hit["code"]: hit for hit in await db.list_hits()}
        return hits, jobs.calls

    hits, calls = asyncio.run(run())
    assert "SSIS-001" not in hits
    assert "SSIS-002" not in hits
    assert hits["SSIS-003"]["status"] == "queued"
    assert hits["SSIS-004"]["status"] == "no_magnet"
    assert calls == [("SSIS-003", "c" * 40, "SSIS-003-UC")]

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

    async def fake_list(settings, sub, known=None):
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

    async def fake_list(settings, sub, known=None):
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


def test_auto_retries_until_a_magnet_matches(tmp_path, monkeypatch):
    notes = []
    ready = {"ok": False}

    async def fake_notice(settings, title, body):
        notes.append(title)

    async def fake_list(settings, sub, known=None):
        return [{"code": "SSIS-009", "title": "later", "western": None}]

    async def fake_search(settings, code, pages=2):
        if not ready["ok"]:
            return []
        return [{
            "info_hash": "c" * 40,
            "title": "SSIS-009-UC",
            "heat": 1,
            "size_bytes": 2 * 1000**3,
            "tags": ["UC"],
        }]

    monkeypatch.setattr("app.follow.send_notice", fake_notice)
    monkeypatch.setattr("app.follow.list_works", fake_list)
    monkeypatch.setattr("app.follow.search_magnets", fake_search)
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        row = new_subscription(
            "actress", "葵", "https://www.javbus.com/star/2xi",
            auto=True, want_uc=True, want_c=False, max_gb=8,
        )
        row["last_check"] = time.time()
        await db.add_subscription(row)
        jobs = _Jobs(settings, db)
        saved = await db.get_subscription(row["id"])
        first = await check_sub(jobs, saved)
        missed = await db.seen_codes(row["id"])
        second = await check_sub(jobs, await db.get_subscription(row["id"]))
        ready["ok"] = True
        third = await check_sub(jobs, await db.get_subscription(row["id"]))
        hits = await db.list_hits()
        seen = await db.seen_codes(row["id"])
        return first, second, third, hits, missed, seen, jobs.calls

    first, second, third, hits, missed, seen, calls = asyncio.run(run())
    assert "SSIS-009" not in missed
    assert first["added"] == 1
    assert second["added"] == 0
    assert third["added"] == 1
    assert len(hits) == 1
    assert hits[0]["status"] == "queued"
    assert "SSIS-009" in seen
    assert notes == ["追更没有符合规则的磁链", "追更已入队"]
    assert calls == [("SSIS-009", "c" * 40, "SSIS-009-UC")]


def test_names_must_match_exactly(monkeypatch):
    from app.sources.tpdb import _exact_name

    rows = [{"id": "1", "name": "Ann Other"}, {"id": "2", "name": "Blake Blossom"}]
    assert _exact_name(rows, "blake blossom")["id"] == "2"
    assert _exact_name(rows, "Blake") is None

    async def fake_fetch(settings, url):
        return '<a href="/star/abc"><img title="别人"></a>'

    monkeypatch.setattr("app.follow.fetch_javbus_html", fake_fetch)

    async def run():
        await resolve_target(Settings(), "actress", "葵つかさ", "")

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "没有找到" in str(exc)
    else:
        raise AssertionError("expected a missing actress")


def test_javbus_follow_reads_the_next_page_until_known(monkeypatch):
    from app.follow import javbus_page_url, list_works

    assert javbus_page_url("https://www.javbus.com/star/2xi", 2) == "https://www.javbus.com/star/2xi/2"
    assert javbus_page_url("https://www.javbus.com/star/2xi/2", 3) == "https://www.javbus.com/star/2xi/3"
    fetched = []

    def card(code):
        return (
            f'<a class="movie-box" href="https://www.javbus.com/{code}">'
            f"<date>{code}</date><date>2024-01-01</date></a>"
        )

    async def fake_fetch(settings, url):
        fetched.append(url)
        if url.endswith("/3"):
            codes = ["SSIS-000"]
        elif url.endswith("/2"):
            codes = ["SSIS-001"]
        else:
            codes = ["SSIS-003", "SSIS-002"]
        return "".join(card(code) for code in codes)

    monkeypatch.setattr("app.follow.fetch_javbus_html", fake_fetch)
    sub = {"kind": "actress", "name": "葵", "target": "https://www.javbus.com/star/2xi"}
    works = asyncio.run(list_works(Settings(javbus_base="https://www.javbus.com"), sub, {"SSIS-001"}))
    assert [item["code"] for item in works] == ["SSIS-003", "SSIS-002", "SSIS-001"]
    assert fetched == [
        "https://www.javbus.com/star/2xi",
        "https://www.javbus.com/star/2xi/2",
    ]


def test_western_follow_stops_on_a_known_page(monkeypatch):
    from app.follow import list_works

    calls = []

    async def fake_id(settings, kind, name):
        return "person-1"

    async def fake_scenes(settings, name, page=1, performer_id=None):
        assert performer_id == "person-1"
        calls.append(page)
        if page == 1:
            return [{"id": "new-id", "title": "New", "site": "Studio", "date": "2024-01-02", "performers": ["A"]}]
        if page == 2:
            return [{"id": "old-id", "title": "Old", "site": "Studio", "date": "2020-01-01", "performers": ["A"]}]
        return [{"id": "older", "title": "Older", "site": "Studio", "date": "2019-01-01", "performers": ["A"]}]

    monkeypatch.setattr("app.follow.catalog_id", fake_id)
    monkeypatch.setattr("app.follow.scenes_for_performer", fake_scenes)
    sub = {"kind": "western_performer", "name": "Blake Blossom", "target": "tpdb:performer:Blake Blossom"}
    works = asyncio.run(list_works(Settings(), sub, {"old-id"}))
    assert [item["code"] for item in works] == ["new-id", "old-id"]
    assert calls == [1, 2]


def test_sync_does_not_wait_for_follow(tmp_path, monkeypatch):
    from app.downloader.jobs import JobManager

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(manager):
        started.set()
        await release.wait()

    monkeypatch.setattr("app.follow.check_due", slow)
    settings = _settings(tmp_path)

    async def run():
        db = Database(settings)
        await db.init()
        mgr = JobManager(settings, db, object(), object())
        await asyncio.wait_for(mgr.sync_all(), 1)
        await asyncio.wait_for(started.wait(), 1)
        release.set()
        await mgr._follow_task

    asyncio.run(run())

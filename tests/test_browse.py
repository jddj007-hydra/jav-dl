import asyncio

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.routers import search, western
from app.sources.javbus import MetadataError
from app.sources.tpdb import fetch_facet


class _Library:
    async def get_many(self, codes):
        return {}

    async def western_many(self, ids):
        return {}


class _DB:
    async def suck_keys(self, kind, keys):
        return set()


def test_jav_browse_reads_the_star_page(monkeypatch):
    seen = []

    async def fake_html(settings, url):
        seen.append(url)
        return """
        <a class="movie-box" href="https://www.javbus.com/SSIS-001">
          <div class="photo-frame"><img src="/pics/thumb/x.jpg" title="SSIS-001 禁欲"></div>
          <div class="photo-info"><span>SSIS-001 禁欲<br><date>SSIS-001</date> / <date>2021-02-18</date></span></div>
        </a>
        """

    monkeypatch.setattr("app.routers.search.fetch_javbus_html", fake_html)
    app = FastAPI()
    app.include_router(search.router)
    app.state.settings = Settings()
    app.state.library = _Library()
    app.state.db = _DB()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.get("/api/jav/browse", params={"url": "https://www.javbus.com/star/2xi/3", "page": 2})
            assert ok.status_code == 200
            body = ok.json()
            assert body["items"][0]["code"] == "SSIS-001"
            genre = await client.get("/api/jav/browse", params={"url": "https://www.javbus.com/genre/3n/4", "page": 2})
            assert genre.status_code == 200
            assert genre.json()["items"][0]["code"] == "SSIS-001"
            bad = await client.get("/api/jav/browse", params={"url": "https://www.javbus.com/SSIS-001"})
            assert bad.status_code == 400

    asyncio.run(run())
    assert seen == [
        "https://www.javbus.com/star/2xi/2",
        "https://www.javbus.com/genre/3n/2",
    ]


def test_jav_browse_rejects_a_foreign_host(monkeypatch):
    async def fake_html(settings, url):
        raise MetadataError("只能打开当前 JavBus 域名下的页面")

    monkeypatch.setattr("app.routers.search.fetch_javbus_html", fake_html)
    app = FastAPI()
    app.include_router(search.router)
    app.state.settings = Settings()
    app.state.library = _Library()
    app.state.db = _DB()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/jav/browse", params={"url": "https://www.javbus.com/studio/7q"})
            assert response.status_code == 400
            assert "JavBus" in response.json()["detail"]

    asyncio.run(run())


def test_keyword_search_drops_excluded_orientation(monkeypatch):
    async def fake_search(settings, query):
        assert query == "葵"
        return [
            {"code": "ABCD-001", "title": "普通作品", "cover": "", "release_date": "", "url": "", "source": "javbus"},
            {"code": "ABCD-002", "title": "男同作品", "cover": "", "release_date": "", "url": "", "source": "javbus"},
            {"code": "ABCD-003", "title": "双性人企划", "cover": "", "release_date": "", "url": "", "source": "javbus"},
        ]

    monkeypatch.setattr("app.routers.search.search_works", fake_search)
    app = FastAPI()
    app.include_router(search.router)
    app.state.settings = Settings()
    app.state.library = _Library()
    app.state.db = _DB()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/search", params={"q": "葵"})
            assert response.status_code == 200
            assert [item["code"] for item in response.json()["items"]] == ["ABCD-001"]

    asyncio.run(run())


def test_western_search_drops_excluded_orientation_and_browse_keeps_it(monkeypatch):
    async def fake_list(settings, kind, page=1, query=None, theme=None, extra=None):
        return {
            "items": [
                {"id": "ok", "duration": "40", "title": "Room", "tags": ["Anal"]},
                {"id": "gay", "duration": "40", "title": "Room", "tags": ["Threesome (Gay)"]},
                {"id": "bi", "duration": "40", "title": "Night", "tags": ["Bisexual"]},
                {"id": "short", "duration": "5", "title": "Clip", "tags": []},
            ],
            "page": 1,
            "last_page": 1,
        }

    async def fake_facet(settings, kind, facet, name, page=1):
        return {
            "items": [
                {"id": "gay", "kind": kind, "duration": "40", "title": "Room", "tags": ["Gay"], "site": name, "performers": []},
            ],
            "page": page,
            "last_page": 1,
        }

    monkeypatch.setattr("app.routers.western.fetch_list", fake_list)
    monkeypatch.setattr("app.routers.western.fetch_facet", fake_facet)
    app = FastAPI()
    app.include_router(western.router)
    app.state.settings = Settings(tpdb_api_key="token")
    app.state.library = _Library()
    app.state.db = _DB()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            found = await client.get("/api/western/search", params={"q": "Room", "kind": "scene"})
            assert found.status_code == 200
            assert [item["id"] for item in found.json()["items"]] == ["ok"]
            opened = await client.get("/api/western/browse", params={"facet": "performer", "name": "Jane Doe"})
            assert opened.status_code == 200
            assert [item["id"] for item in opened.json()["items"]] == ["gay"]

    asyncio.run(run())


def test_western_browse_uses_the_exact_performer(monkeypatch):
    seen = {}

    async def fake_facet(settings, kind, facet, name, page=1):
        seen.update(kind=kind, facet=facet, name=name, page=page)
        return {
            "items": [{
                "id": "abc",
                "kind": kind,
                "title": "A scene",
                "date": "2024-01-02",
                "site": "Vixen",
                "performers": ["Jane Doe"],
                "cover": "",
                "duration": "20",
                "tags": [],
            }],
            "page": page,
            "last_page": 4,
        }

    monkeypatch.setattr("app.routers.western.fetch_facet", fake_facet)
    app = FastAPI()
    app.include_router(western.router)
    app.state.settings = Settings(tpdb_api_key="token")
    app.state.library = _Library()
    app.state.db = _DB()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.get("/api/western/browse", params={
                "facet": "performer", "name": "Jane Doe", "kind": "scene", "page": 2,
            })
            assert ok.status_code == 200
            body = ok.json()
            assert body["items"][0]["id"] == "abc"
            assert body["last_page"] == 4
            missing = await client.get("/api/western/browse", params={"facet": "tag", "name": "Jane"})
            assert missing.status_code == 400
            app.state.settings = Settings()
            quiet = await client.get("/api/western/browse", params={"facet": "site", "name": "Vixen"})
            assert quiet.status_code == 200
            assert "ThePornDB" in quiet.json()["error"]

    asyncio.run(run())
    assert seen == {"kind": "scene", "facet": "performer", "name": "Jane Doe", "page": 2}


def test_fetch_facet_asks_theporndb_by_id(monkeypatch):
    async def fake_named(settings, path, name):
        assert path == "/sites"
        assert name == "Vixen"
        return "site-1"

    async def fake_json(settings, path, params=None):
        assert path == "/sites/site-1/movies"
        assert params == {"page": 1, "per_page": 24}
        return {"data": [{"id": "m1", "title": "Movie", "duration": 2000}], "meta": {"last_page": 2}}

    monkeypatch.setattr("app.sources.tpdb._named_id", fake_named)
    monkeypatch.setattr("app.sources.tpdb._get_json", fake_json)

    async def run():
        payload = await fetch_facet(Settings(), "movie", "site", "Vixen", 1)
        assert payload["items"][0]["id"] == "m1"
        assert payload["last_page"] == 2

    asyncio.run(run())

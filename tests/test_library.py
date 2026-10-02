import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.db import Database
import pytest
from fastapi import HTTPException

from app.library import (
    Library,
    attach_library,
    attach_western,
    library_info,
    poster_file,
    scan_media,
    scan_western,
)
from app.nfo import build_nfo
from app.routers.library_page import library_page, library_poster


def test_scan_media_indexes_code_folders(tmp_path):
    folder = tmp_path / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "SSIS-001.mp4").write_bytes(b"x")
    (folder / "SSIS-001.nfo").write_text("<movie/>", encoding="utf-8")
    (folder / "poster.jpg").write_bytes(b"jpg")
    empty = tmp_path / "202103" / "IPX-001"
    empty.mkdir(parents=True)
    (empty / "poster.jpg").write_bytes(b"jpg")
    rows = scan_media(tmp_path)
    by_code = {r["code"]: r for r in rows}
    assert "SSIS-001" in by_code
    assert by_code["SSIS-001"]["path"] == "202102/SSIS-001"
    assert by_code["SSIS-001"]["has_nfo"] == 1
    assert by_code["SSIS-001"]["has_poster"] == 1
    assert "IPX-001" not in by_code


def test_scan_media_reads_nfo_fields(tmp_path):
    folder = tmp_path / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    video = folder / "SSIS-001.mp4"
    video.write_bytes(b"x")
    (folder / "SSIS-001.nfo").write_text(build_nfo({
        "code": "SSIS-001",
        "title": "Title",
        "release_date": "2021-02-18",
        "actors": [
            {"name": "葵つかさ", "photo": "https://www.javbus.com/pics/actress/2xi_a.jpg"},
            "乙白さやか",
        ],
    }), encoding="utf-8")
    row = scan_media(tmp_path)[0]
    assert row["title"] == "Title"
    assert row["actors"] == ["葵つかさ", "乙白さやか"]
    assert {path.name for path in folder.iterdir()} == {"SSIS-001.mp4", "SSIS-001.nfo"}
    assert row["release_date"] == "2021-02-18"
    assert row["added_at"] == video.stat().st_mtime


def test_poster_file_stays_inside_root(tmp_path):
    folder = tmp_path / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"jpg")
    (tmp_path / "Studio").mkdir()
    (tmp_path / "Studio" / "clip-poster.jpg").write_bytes(b"jpg")
    (tmp_path.parent / "poster.jpg").write_bytes(b"secret")
    assert poster_file(tmp_path, "202102/SSIS-001", "jav") == folder / "poster.jpg"
    assert poster_file(tmp_path, "Studio/clip.mp4", "western") == tmp_path / "Studio" / "clip-poster.jpg"
    assert poster_file(tmp_path, "vr/Studio/clip.mp4", "vr") == tmp_path / "Studio" / "clip-poster.jpg"
    assert poster_file(tmp_path, "..", "jav") is None
    assert poster_file(tmp_path, "202102/../..", "jav") is None
    assert poster_file(tmp_path, str(tmp_path.parent), "jav") is None
    assert poster_file(tmp_path, "202102\\..\\..", "jav") is None
    assert poster_file(None, "202102/SSIS-001", "jav") is None


def test_library_poster_404_without_file(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", download_dir=tmp_path / "dl", media_dir=tmp_path / "media")
    settings.ensure_dirs()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(library_poster(request, path="../data", kind="jav"))
    assert exc.value.status_code == 404


def test_library_poster_western_kind_finds_vr_file(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "west"),
        vr_media_dir=str(tmp_path / "vrporn" / "western"),
    )
    settings.ensure_dirs()
    settings.vr_root.mkdir(parents=True)
    poster = settings.vr_root / "Studio" / "headset-poster.jpg"
    poster.parent.mkdir(parents=True)
    poster.write_bytes(b"jpg")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    response = asyncio.run(library_poster(request, path="vr/Studio/headset.mp4", kind="western"))
    assert response.path == poster


def test_scan_media_accepts_fanart_as_poster(tmp_path):
    folder = tmp_path / "DSVR" / "DSVR-1124"
    folder.mkdir(parents=True)
    (folder / "DSVR-1124.mp4").write_bytes(b"x")
    (folder / "fanart.jpg").write_bytes(b"jpg")
    row = scan_media(tmp_path)[0]
    assert row["has_poster"] == 1
    assert row["poster"] == "DSVR/DSVR-1124/fanart.jpg"


def test_scan_media_indexes_uncensored_date_codes(tmp_path):
    from app.catalog import movie_shelf

    folder = tmp_path / "Caribbean" / "021622-001"
    folder.mkdir(parents=True)
    (folder / "021622-001.mp4").write_bytes(b"x")
    row = scan_media(tmp_path)[0]
    assert row["code"] == "021622-001"
    assert row["month"] == "Caribbean"
    assert row["path"] == "Caribbean/021622-001"
    assert movie_shelf(row["code"], row["month"]) == "vr"


def test_scan_prefers_newer_month(tmp_path):
    old = tmp_path / "202101" / "SSIS-001"
    new = tmp_path / "202102" / "SSIS-001"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "a.mp4").write_bytes(b"x")
    (new / "b.mp4").write_bytes(b"x")
    rows = scan_media(tmp_path)
    assert len(rows) == 1
    assert rows[0]["path"] == "202102/SSIS-001"


def test_scan_western_reads_tpdb_id(tmp_path):
    studio = tmp_path / "Brazzers"
    studio.mkdir()
    (studio / "clip.mp4").write_bytes(b"x")
    (studio / "clip-poster.jpg").write_bytes(b"j")
    (studio / "clip.nfo").write_text(
        '<movie><originaltitle>Scene</originaltitle>'
        '<uniqueid type="tpdb">abc</uniqueid></movie>',
        encoding="utf-8",
    )
    (tmp_path / "note.txt").write_text("skip", encoding="utf-8")
    rows = scan_western(tmp_path)
    assert len(rows) == 1
    assert rows[0]["tpdb_id"] == "abc"
    assert rows[0]["studio"] == "Brazzers"
    assert rows[0]["title"] == "Scene"
    assert rows[0]["path"] == "Brazzers/clip.mp4"
    assert rows[0]["has_poster"] == 1
    assert rows[0]["shelf"] == "western"

    vr = tmp_path / "vr" / "VRBangers"
    vr.mkdir(parents=True)
    (vr / "headset.mp4").write_bytes(b"x")
    nested = tmp_path / "vr" / "SqueezeVR" / "Fist Time"
    nested.mkdir(parents=True)
    (nested / "clip.mp4").write_bytes(b"x")
    (nested / "movie.nfo").write_text(
        '<movie><originaltitle>Fist</originaltitle>'
        '<uniqueid type="tpdb">vr1</uniqueid></movie>',
        encoding="utf-8",
    )
    (nested / "poster.jpg").write_bytes(b"j")
    vr_rows = {row["path"]: row for row in scan_western(tmp_path / "vr", "vr")}
    assert vr_rows["vr/VRBangers/headset.mp4"]["shelf"] == "vr"
    nested_row = vr_rows["vr/SqueezeVR/Fist Time/clip.mp4"]
    assert nested_row["title"] == "Fist"
    assert nested_row["tpdb_id"] == "vr1"
    assert nested_row["has_nfo"] == 1
    assert nested_row["has_poster"] == 1
    assert nested_row["poster"] == "vr/SqueezeVR/Fist Time/poster.jpg"


def test_attach_western_marks_present():
    items = attach_western(
        [{"id": "abc", "title": "Scene"}, {"id": "nope", "title": "Other"}],
        {"abc": {"path": "Brazzers/clip.mp4", "has_nfo": 1}},
    )
    assert items[0]["library"]["present"] is True
    assert items[0]["library"]["path"] == "Brazzers/clip.mp4"
    assert items[1]["library"]["present"] is False


def test_library_page_groups_jav_by_month_and_western_by_studio(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "west"),
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        lib = Library(settings, db)
        await db.upsert_library({
            "code": "SSIS-001", "month": "202102", "path": "202102/SSIS-001", "has_video": 1,
            "title": "Title", "actors": [{"name": "葵つかさ"}], "release_date": "2021-02-18",
            "studio": "S1", "series": "S1 Girls",
        })
        await db.upsert_library({
            "code": "IPX-001", "month": "202101", "path": "202101/IPX-001", "has_video": 1,
        })
        await db.upsert_library({
            "code": "DSVR-1124", "month": "DSVR", "path": "DSVR/DSVR-1124", "has_video": 1,
        })
        await db.upsert_library({
            "code": "021622-001", "month": "Caribbean", "path": "Caribbean/021622-001", "has_video": 1,
        })
        await lib.remember_western({"entries": [{
            "path": "Brazzers/a.mp4",
            "tpdb_id": "abc",
            "studio": "Brazzers",
            "title": "Scene",
            "has_nfo": 1,
            "has_poster": 0,
        }]})
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, db=db)))
        body = await library_page(request)
        assert [group["month"] for group in body["jav"]] == ["202102", "202101"]
        assert [group["month"] for group in body["jav_vr"]] == ["Caribbean", "DSVR"]
        assert body["jav_vr"][0]["items"][0]["code"] == "021622-001"
        assert body["jav_vr"][1]["items"][0]["code"] == "DSVR-1124"
        first = body["jav"][0]["items"][0]
        assert first["full_path"].endswith("202102/SSIS-001")
        assert first["title"] == "Title"
        assert first["actors"] == ["葵つかさ"]
        assert first["studio"] == "S1"
        assert first["series"] == "S1 Girls"
        assert first["release_date"] == "2021-02-18"
        assert first["added_at"] > 0
        assert body["western"][0]["studio"] == "Brazzers"
        assert body["western"][0]["items"][0]["full_path"].endswith("Brazzers/a.mp4")
        assert body["western"][0]["items"][0]["studio"] == "Brazzers"
        assert "series" not in body["western"][0]["items"][0]
        assert (await db.western_by_ids(["abc"]))["abc"]["title"] == "Scene"

    asyncio.run(run())


def test_attach_library():
    hits = {"SSIS-001": {"path": "202102/SSIS-001", "has_video": 1, "has_nfo": 1, "has_poster": 0}}
    items = attach_library(
        [{"code": "SSIS-001", "title": "a"}, {"code": "IPX-001", "title": "b"}],
        hits,
    )
    assert items[0]["library"]["present"] is True
    assert items[0]["library"]["path"] == "202102/SSIS-001"
    assert items[1]["library"]["present"] is False
    assert library_info(None) == {"present": False}


def test_refresh_scrapes_missing_vr_sidecars(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "west"),
        vr_media_dir=str(tmp_path / "vrporn" / "western"),
        scrape_min_mb=0,
        tpdb_api_key="token",
    )
    settings.ensure_dirs()
    settings.jav_vr_root.mkdir(parents=True)
    settings.vr_root.mkdir(parents=True)
    jav = settings.jav_vr_root / "DSVR" / "DSVR-1124"
    jav.mkdir(parents=True)
    (jav / "DSVR-1124.mp4").write_bytes(b"x" * 8)
    west = settings.vr_root / "VRBangers"
    west.mkdir()
    (west / "headset.mp4").write_bytes(b"x" * 8)

    async def fake_jav(settings, db, dest_dir, code):
        (dest_dir / f"{code}.nfo").write_text("<movie><title>Headset</title></movie>", encoding="utf-8")
        (dest_dir / "poster.jpg").write_bytes(b"j")
        from app.library import index_code_dir
        return index_code_dir(dest_dir, dest_dir.parent.name)

    async def fake_west(settings, video):
        video.with_name(f"{video.stem}.nfo").write_text(
            '<movie><title>Office</title><uniqueid type="tpdb">vr1</uniqueid></movie>',
            encoding="utf-8",
        )
        video.with_name(f"{video.stem}-poster.jpg").write_bytes(b"p")
        return True

    monkeypatch.setattr("app.scrape.fill_jav_folder", fake_jav)
    monkeypatch.setattr("app.western_archive.fill_western_video", fake_west)

    async def run():
        db = Database(settings)
        await db.init()
        lib = Library(settings, db)
        count = await lib.refresh(scrape_missing=True)
        assert count == 2
        jav_row = await db.get_library("DSVR-1124")
        assert jav_row["has_nfo"] == 1
        assert jav_row["has_poster"] == 1
        west_rows = await db.list_western()
        assert west_rows[0]["has_nfo"] == 1
        assert west_rows[0]["has_poster"] == 1
        assert west_rows[0]["title"] == "Office"

    asyncio.run(run())


def test_refresh_does_not_rewrite_an_existing_poster(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        vr_media_dir=str(tmp_path / "vrporn" / "western"),
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    settings.jav_vr_root.mkdir(parents=True)
    folder = settings.jav_vr_root / "DSVR" / "DSVR-1124"
    folder.mkdir(parents=True)
    (folder / "DSVR-1124.mp4").write_bytes(b"video")
    (folder / "poster.jpg").write_bytes(b"keep-poster")
    (folder / "fanart.jpg").write_bytes(b"keep-fanart")
    (folder / "DSVR-1124.nfo").write_text(
        "<movie><originaltitle>Headset</originaltitle></movie>",
        encoding="utf-8",
    )

    async def run():
        db = Database(settings)
        await db.init()
        count = await Library(settings, db).refresh(scrape_missing=True)
        assert count == 1

    asyncio.run(run())
    assert (folder / "poster.jpg").read_bytes() == b"keep-poster"
    assert (folder / "fanart.jpg").read_bytes() == b"keep-fanart"
    assert (folder / "DSVR-1124.mp4").read_bytes() == b"video"


def test_drop_version_removes_files_and_keeps_suck_and_plays(tmp_path):
    import aiosqlite
    import httpx
    from fastapi import FastAPI

    from app.routers import downloads, library_page

    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "west"),
    )
    settings.ensure_dirs()
    folder = settings.media_dir / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "SSIS-001.mp4").write_bytes(b"video")
    (folder / "SSIS-001.nfo").write_text("<movie></movie>", encoding="utf-8")
    (folder / "poster.jpg").write_bytes(b"p")
    kept = settings.media_dir / "202102" / "IPX-001"
    kept.mkdir()
    (kept / "IPX-001.mp4").write_bytes(b"keep")
    studio = settings.western_root / "Studio"
    studio.mkdir(parents=True)
    (studio / "clip.mp4").write_bytes(b"w")
    (studio / "clip.nfo").write_text("n", encoding="utf-8")
    (studio / "clip-poster.jpg").write_bytes(b"p")
    (studio / "other.mp4").write_bytes(b"o")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "x").write_text("no", encoding="utf-8")

    class Jobs:
        def __init__(self):
            self.called = False

        async def prepare_files(self, *args, **kwargs):
            self.called = True
            return {"mode": "direct", "job": {"id": "job", "dest": str(settings.download_dir)}}

        async def enqueue(self, *args, **kwargs):
            self.called = True
            return {"id": "job"}

        async def cancel_files(self, token):
            self.called = True

    jobs = Jobs()
    app = FastAPI()
    app.include_router(library_page.router)
    app.include_router(downloads.router)

    async def plays(db):
        async with aiosqlite.connect(db.path) as conn:
            cur = await conn.execute("SELECT kind, key FROM plays ORDER BY kind, key")
            return await cur.fetchall()

    async def run():
        db = Database(settings)
        await db.init()
        await db.upsert_library({
            "code": "SSIS-001",
            "month": "202102",
            "path": "202102/SSIS-001",
            "has_video": 1,
            "title": "旧版",
        })
        await db.upsert_library({
            "code": "SSIS-009",
            "month": "202102",
            "path": "../secret",
            "has_video": 1,
            "title": "越界",
        })
        await db.upsert_western({
            "path": "Studio/clip.mp4",
            "tpdb_id": "scene-1",
            "studio": "Studio",
            "title": "Clip",
            "has_nfo": 1,
            "has_poster": 1,
        })
        await db.mark_played("jav", "SSIS-001")
        await db.mark_played("western", "scene-1")
        app.state.db = db
        app.state.settings = settings
        app.state.jobs = jobs
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            removed = await client.post("/api/library/remove", json={"kind": "jav", "key": "ssis001"})
            assert removed.status_code == 200
            assert removed.json()["item"]["removed"] is True
            west = await client.post("/api/library/remove", json={"kind": "western", "key": "scene-1"})
            assert west.status_code == 200
            escaped = await client.post("/api/library/remove", json={"kind": "jav", "key": "SSIS-009"})
            assert escaped.status_code == 400
            again = await client.post("/api/downloads/files", json={
                "code": "SSIS-001",
                "info_hash": "a" * 40,
                "title": "旧版",
            })
            assert again.status_code == 200
            page = await client.get("/api/library")
            body = page.json()
        assert await db.get_library("SSIS-001") is None
        assert await db.get_library("SSIS-009") is not None
        assert (await db.western_by_ids(["scene-1"])) == {}
        assert await db.list_suck() == []
        assert await plays(db) == [("jav", "SSIS-001"), ("western", "scene-1")]
        assert body["suck"] == []
        assert jobs.called is True

    asyncio.run(run())
    assert not folder.exists()
    assert (kept / "IPX-001.mp4").read_bytes() == b"keep"
    assert not (studio / "clip.mp4").exists()
    assert not (studio / "clip.nfo").exists()
    assert not (studio / "clip-poster.jpg").exists()
    assert (studio / "other.mp4").read_bytes() == b"o"
    assert (secret / "x").read_text(encoding="utf-8") == "no"


def test_library_card_offers_delete_without_removing_rescrape():
    root = __import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "static"
    script = (root / "app.js").read_text(encoding="utf-8")
    assert "删除此版本" in script
    assert "文件会删掉" in script
    assert 'data-act="rescrape"' in script
    assert "/api/library/remove" in script

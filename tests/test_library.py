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
        "actors": [{"name": "葵つかさ"}, "乙白さやか"],
    }), encoding="utf-8")
    row = scan_media(tmp_path)[0]
    assert row["title"] == "Title"
    assert row["actors"] == ["葵つかさ", "乙白さやか"]
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
        })
        await db.upsert_library({
            "code": "IPX-001", "month": "202101", "path": "202101/IPX-001", "has_video": 1,
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
        first = body["jav"][0]["items"][0]
        assert first["full_path"].endswith("202102/SSIS-001")
        assert first["title"] == "Title"
        assert first["actors"] == ["葵つかさ"]
        assert first["release_date"] == "2021-02-18"
        assert first["added_at"] > 0
        assert body["western"][0]["studio"] == "Brazzers"
        assert body["western"][0]["items"][0]["full_path"].endswith("Brazzers/a.mp4")
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

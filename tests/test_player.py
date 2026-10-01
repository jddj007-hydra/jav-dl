import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.catalog import library_stamp, movie_facets, movie_item, query_movies, query_scenes, scene_item
from app.config import Settings
from app.db import Database
from app.library import Library, scan_media, scan_western
from app.models import PlayMark
from app.routers.player import player_movies, player_played, player_scenes, player_stamp


def test_scan_media_keeps_the_feature_and_skips_the_sample(tmp_path):
    folder = tmp_path / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "sample.mp4").write_bytes(b"tiny")
    feature = folder / "SSIS-001-C.mp4"
    feature.write_bytes(b"feature-bytes")
    (folder / "SSIS-001.nfo").write_text(
        "<movie><title>CODE Title</title><originaltitle>正片</originaltitle>"
        "<studio>S1</studio><runtime>120</runtime><plot>简介</plot>"
        "<premiered>2021-02-18</premiered>"
        "<genre>中文字幕</genre><genre>无码</genre><tag>中文字幕</tag>"
        "<set><name>濃密</name></set>"
        "<actor><name>葵つかさ</name></actor></movie>",
        encoding="utf-8",
    )
    (folder / "poster.jpg").write_bytes(b"jpg")
    row = scan_media(tmp_path)[0]
    assert row["video"] == "202102/SSIS-001/SSIS-001-C.mp4"
    assert row["video_count"] == 1
    assert row["video_size"] == len(b"feature-bytes")
    assert row["poster"] == "202102/SSIS-001/poster.jpg"
    assert row["studio"] == "S1"
    assert row["genres"] == ["中文字幕", "无码"]
    assert row["outline"] == "简介"
    assert row["runtime_min"] == 120
    assert row["has_sub"] == 1
    assert row["has_uncensored"] == 1
    assert row["title"] == "正片"
    assert row["series"] == "濃密"


def test_scan_media_marks_a_cracked_filename(tmp_path):
    folder = tmp_path / "202001" / "IPX-001"
    folder.mkdir(parents=True)
    (folder / "IPX-001-破解.mp4").write_bytes(b"x")
    row = scan_media(tmp_path)[0]
    assert row["has_cracked"] == 1
    assert row["has_sub"] == 0


def test_scan_western_adds_playback_fields(tmp_path):
    studio = tmp_path / "Brazzers"
    studio.mkdir()
    (studio / "Brazzers.20.10.24.scene.1080p.mp4").write_bytes(b"x")
    (studio / "Brazzers.20.10.24.scene.1080p-poster.jpg").write_bytes(b"j")
    (studio / "Brazzers.20.10.24.scene.1080p.nfo").write_text(
        "<movie><title>Scene</title><runtime>32</runtime>"
        "<premiered>2020-10-24</premiered>"
        "<actor><name>Jane</name></actor></movie>",
        encoding="utf-8",
    )
    row = scan_western(tmp_path)[0]
    assert row["runtime_min"] == 32
    assert row["year"] == 2020
    assert row["resolution"] == "1080p"
    assert row["poster"] == "Brazzers/Brazzers.20.10.24.scene.1080p-poster.jpg"
    assert row["actors"] == ["Jane"]


def test_player_query_filters_subtitle_and_actor():
    rows = [
        {
            "code": "SSIS-001",
            "month": "202102",
            "path": "202102/SSIS-001",
            "title": "正片",
            "actors": '["葵つかさ"]',
            "genres": '["中文字幕"]',
            "studio": "S1",
            "release_date": "2021-02-18",
            "outline": "简介",
            "runtime_min": 120,
            "has_sub": 1,
            "has_uncensored": 0,
            "has_cracked": 0,
            "video": "202102/SSIS-001/SSIS-001-C.mp4",
            "video_count": 1,
            "video_size": 10,
            "poster": "202102/SSIS-001/poster.jpg",
            "has_poster": 1,
            "added_at": 20,
        },
        {
            "code": "IPX-001",
            "month": "202001",
            "path": "202001/IPX-001",
            "title": "另一部",
            "actors": "[]",
            "genres": "[]",
            "studio": "IdeaPocket",
            "release_date": "2020-01-02",
            "outline": "",
            "runtime_min": 0,
            "has_sub": 0,
            "has_uncensored": 0,
            "has_cracked": 1,
            "video": "202001/IPX-001/IPX-001.mp4",
            "video_count": 1,
            "video_size": 4,
            "poster": "",
            "has_poster": 0,
            "added_at": 10,
        },
    ]
    body = query_movies(rows, {
        "q": "葵",
        "actor": "葵",
        "genre": "字幕",
        "studio": "s1",
        "year_month": "202102",
        "subtitle": True,
        "uncensored": False,
        "cracked": False,
        "sort_field": "premiered",
        "descending": True,
        "offset": 0,
        "limit": 0,
    })
    assert body["total"] == 2
    assert body["matched"] == 1
    assert body["paths"] == "relative"
    item = body["items"][0]
    assert item["code"] == "SSIS-001"
    assert item["video"] == "202102/SSIS-001/SSIS-001-C.mp4"
    assert item["poster_url"].startswith("/api/library/poster?kind=jav&path=")
    assert item["runtime_min"] == 120
    assert item["shelf"] == "flat"
    assert movie_facets(rows)["studios"] == [
        {"name": "IdeaPocket", "count": 1},
        {"name": "S1", "count": 1},
    ]
    scenes = query_scenes([{
        "path": "Brazzers/clip.1080p.mp4",
        "studio": "Brazzers",
        "title": "Scene",
        "actors": '["Jane"]',
        "release_date": "2020-10-24",
        "year": 2020,
        "runtime_min": 32,
        "resolution": "1080p",
        "poster": "Brazzers/clip.1080p-poster.jpg",
        "has_poster": 1,
        "tpdb_id": "abc",
        "added_at": 3,
    }], {
        "q": "jane",
        "performer": "Jane",
        "site": "brazzers",
        "year": 2020,
        "sort_field": "title",
        "descending": False,
        "offset": 0,
        "limit": 0,
    })
    assert scenes["matched"] == 1
    assert scenes["items"][0]["release_name"] == "clip.1080p"
    assert scenes["items"][0]["folder"] == "Brazzers"
    assert scenes["items"][0]["shelf"] == "flat"
    assert "full_path" not in scenes["items"][0]


def test_vr_scene_poster_url_uses_vr_kind():
    item = scene_item({
        "path": "vr/Studio/headset.mp4",
        "shelf": "vr",
        "studio": "Studio",
        "title": "Headset",
        "poster": "vr/Studio/headset-poster.jpg",
        "has_poster": 1,
    })
    assert item["shelf"] == "vr"
    assert item["poster_url"].startswith("/api/library/poster?kind=vr&path=")
    assert "headset.mp4" in item["poster_url"]


def test_player_route_reads_the_scanned_library(tmp_path):
    media = tmp_path / "media"
    folder = media / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "SSIS-001.mp4").write_bytes(b"x")
    (folder / "SSIS-001.nfo").write_text(
        "<movie><originaltitle>正片</originaltitle><studio>S1</studio></movie>",
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=media,
        western_media_dir=str(tmp_path / "west"),
    )
    settings.ensure_dirs()

    async def run():
        db = Database(settings)
        await db.init()
        await Library(settings, db).refresh()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
        body = await player_movies(request, q="正片")
        assert body["matched"] == 1
        assert body["items"][0]["studio"] == "S1"
        assert body["items"][0]["video"] == "202102/SSIS-001/SSIS-001.mp4"
        assert body["items"][0]["last_played_at"] == 0
        assert body["items"][0]["shelf"] == "flat"
        played = await player_played(PlayMark(kind="jav", key="ssis-001"), request)
        assert played["key"] == "SSIS-001"
        assert played["last_played_at"] > 0
        again = await player_movies(request, q="正片")
        assert again["items"][0]["last_played_at"] == int(played["last_played_at"])
        await Library(settings, db).refresh()
        kept = await player_movies(request, q="正片")
        assert kept["items"][0]["last_played_at"] == int(played["last_played_at"])
        with pytest.raises(HTTPException) as missing:
            await player_played(PlayMark(kind="jav", key="ABCD-999"), request)
        assert missing.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await player_movies(request, sort="nope")
        assert exc.value.status_code == 400

    asyncio.run(run())


def test_player_items_carry_shelf():
    assert movie_item({"code": "SSIS-001", "path": "202102/DSVR-999"})["shelf"] == "flat"
    assert movie_item({"code": "DSVR-1124"})["shelf"] == "vr"
    assert scene_item({"path": "DSVR/clip.mp4", "shelf": "western"})["shelf"] == "flat"
    assert scene_item({"path": "vr/Studio/x.mp4"})["shelf"] == "vr"
    assert scene_item({"path": "Studio/x.mp4", "shelf": "western"})["shelf"] == "flat"


def test_player_query_can_filter_by_shelf():
    rows = [
        {"code": "SSIS-001", "title": "平面", "path": "202102/SSIS-001"},
        {"code": "DSVR-1124", "title": "headset", "path": "DSVR/DSVR-1124"},
    ]
    spec = {
        "q": "",
        "actor": "",
        "genre": "",
        "studio": "",
        "year_month": "",
        "subtitle": False,
        "uncensored": False,
        "cracked": False,
        "sort_field": "code",
        "descending": False,
        "offset": 0,
        "limit": 0,
    }
    whole = query_movies(rows, spec)
    assert [item["code"] for item in whole["items"]] == ["DSVR-1124", "SSIS-001"]
    vr = query_movies(rows, {**spec, "format": "vr"})
    assert vr["total"] == 1
    assert vr["items"][0]["code"] == "DSVR-1124"
    assert vr["items"][0]["shelf"] == "vr"
    flat = query_movies(rows, {**spec, "format": "flat"})
    assert [item["code"] for item in flat["items"]] == ["SSIS-001"]
    scenes = [
        {"path": "Brazzers/clip.mp4", "shelf": "western", "title": "flat-scene"},
        {"path": "vr/Studio/x.mp4", "shelf": "vr", "title": "headset"},
    ]
    scene_spec = {
        "q": "",
        "performer": "",
        "site": "",
        "year": 0,
        "sort_field": "title",
        "descending": False,
        "offset": 0,
        "limit": 0,
    }
    vr_scenes = query_scenes(scenes, {**scene_spec, "format": "vr"})
    assert vr_scenes["total"] == 1
    assert vr_scenes["items"][0]["video"] == "vr/Studio/x.mp4"
    assert vr_scenes["items"][0]["shelf"] == "vr"
    all_scenes = query_scenes(scenes, {**scene_spec, "format": "all"})
    assert all_scenes["total"] == 2


def test_player_stamp_tracks_catalog_not_playback():
    empty = library_stamp([], [])
    assert empty["movies"] == 0
    assert empty["scenes"] == 0
    assert empty == library_stamp([], [])
    movie = {
        "code": "SSIS-001",
        "path": "202102/SSIS-001",
        "title": "正片",
        "video": "202102/SSIS-001/SSIS-001.mp4",
        "video_size": 10,
        "added_at": 20,
    }
    added = library_stamp([movie], [])
    assert added["catalog"] != empty["catalog"]
    assert added["played"] == empty["played"]
    assert added["movies"] == 1
    watched = library_stamp([{**movie, "last_played_at": 99}], [])
    assert watched["catalog"] == added["catalog"]
    assert watched["played"] != added["played"]
    scraped = library_stamp([{**movie, "title": "新标题"}], [])
    assert scraped["catalog"] != added["catalog"]
    assert scraped["played"] == added["played"]


def test_player_stamp_route_follows_library_changes(tmp_path):
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
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
        empty = await player_stamp(request)
        movies = await player_movies(request)
        scenes = await player_scenes(request)
        assert empty["movies"] == 0
        assert empty["scenes"] == 0
        assert movies["catalog"] == empty["catalog"]
        assert movies["played"] == empty["played"]
        assert movies["movies"] == 0
        assert scenes["catalog"] == empty["catalog"]
        assert scenes["scenes"] == 0
        await db.upsert_library({
            "code": "SSIS-001",
            "month": "202102",
            "path": "202102/SSIS-001",
            "title": "正片",
            "video": "202102/SSIS-001/SSIS-001.mp4",
            "video_size": 10,
        })
        await db.upsert_library({
            "code": "DSVR-1124",
            "month": "202401",
            "path": "DSVR/DSVR-1124",
            "title": "headset",
            "video": "DSVR/DSVR-1124/DSVR-1124.mp4",
            "video_size": 8,
        })
        await db.upsert_western({
            "path": "vr/Studio/x.mp4",
            "shelf": "vr",
            "studio": "Studio",
            "title": "headset",
            "tpdb_id": "west-1",
        })
        added = await player_stamp(request)
        listed = await player_movies(request)
        vr_only = await player_movies(request, format="vr")
        vr_scenes = await player_scenes(request, format="vr")
        assert added["catalog"] != empty["catalog"]
        assert added["played"] == empty["played"]
        assert added["movies"] == 2
        assert added["scenes"] == 1
        assert listed["catalog"] == added["catalog"]
        assert listed["played"] == added["played"]
        assert listed["movies"] == 2
        assert listed["scenes"] == 1
        assert {item["code"]: item["shelf"] for item in listed["items"]} == {
            "SSIS-001": "flat",
            "DSVR-1124": "vr",
        }
        assert vr_only["total"] == 1
        assert vr_only["items"][0]["code"] == "DSVR-1124"
        assert vr_only["catalog"] == added["catalog"]
        assert vr_scenes["items"][0]["shelf"] == "vr"
        assert vr_scenes["items"][0]["video"] == "vr/Studio/x.mp4"
        played = await player_played(PlayMark(kind="jav", key="ssis-001"), request)
        after = await player_stamp(request)
        assert after["catalog"] == added["catalog"]
        assert after["played"] != added["played"]
        assert int(played["last_played_at"]) > 0
        with pytest.raises(HTTPException) as exc:
            await player_movies(request, format="nope")
        assert exc.value.status_code == 400

    asyncio.run(run())

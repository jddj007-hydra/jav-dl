import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.batch import enqueue_batch, parse_batch_codes, preview_batch
from app.config import Settings
from app.downloader.aria2 import Aria2Error
from app.models import BatchEnqueue, BatchText
from app.routers import downloads
from app.sources.clm import MagnetSearchError
from app.sources.javbus import MetadataError


def test_parse_batch_codes_keeps_paste_order_and_skips_junk():
    text = "ssis001, MIDV-123\nHD-1080  ipx_001\n再看 ABP-123 和 ssis-001"
    assert parse_batch_codes(text) == ["SSIS-001", "MIDV-123", "IPX-001", "ABP-123"]
    assert parse_batch_codes("没有番号") == []
    assert parse_batch_codes("") == []
    assert parse_batch_codes("092126-001 FC2-3237415") == ["092126-001", "FC2-PPV-3237415"]


def test_parse_batch_codes_keeps_full_fc2_lines():
    text = (
        "FC2-PPV-4587943\n"
        "FC2-PPV-4825364\n"
        "FC2-PPV-4121738\n"
        "FC2-PPV-4733095\n"
        "FC2-PPV-4741172\n"
        "FC2-PPV-4595631"
    )
    assert parse_batch_codes(text) == [
        "FC2-PPV-4587943",
        "FC2-PPV-4825364",
        "FC2-PPV-4121738",
        "FC2-PPV-4733095",
        "FC2-PPV-4741172",
        "FC2-PPV-4595631",
    ]
    assert parse_batch_codes("FC2PPV4587943, SSIS-001 FC2-4825364") == [
        "FC2-PPV-4587943",
        "SSIS-001",
        "FC2-PPV-4825364",
    ]


def test_preview_picks_the_existing_sort_and_skips_empties(monkeypatch):
    async def fake_search(settings, code, pages=2):
        if code == "SSIS-001":
            return [
                {
                    "info_hash": "b" * 40,
                    "title": "SSIS-001 plain",
                    "heat": 100,
                    "size": "1GB",
                    "tags": [],
                },
                {
                    "info_hash": "a" * 40,
                    "title": "SSIS-001-UC",
                    "heat": 1,
                    "size": "2GB",
                    "tags": ["UC"],
                },
            ]
        if code == "MIDV-001":
            return []
        if code == "ABCD-001":
            raise MagnetSearchError("磁力猫请求失败")
        return [{
            "info_hash": "c" * 40,
            "title": "IPX-001",
            "heat": 3,
            "size": "800MB",
        }]

    async def fake_meta(settings, code):
        if code == "SSIS-001":
            return {
                "title": "SSIS meta",
                "cover": "https://example.com/ssis.jpg",
                "release_date": "2021-01-01",
                "runtime": "120分",
                "studio": "S1",
                "actors": [{"name": "葵つかさ"}, {"name": "Extra"}],
                "samples": [{"thumb": "https://example.com/s.jpg"}],
            }
        if code == "MIDV-001":
            raise MetadataError("未找到该番号", 404)
        return {"title": code, "cover": "", "actors": []}

    class Lib:
        async def get_many(self, codes):
            return {
                "SSIS-001": {
                    "code": "SSIS-001",
                    "path": "202101/SSIS-001",
                    "has_video": 1,
                    "has_nfo": 1,
                    "has_poster": 1,
                },
            }

    class Db:
        async def suck_keys(self, kind, keys):
            assert kind == "jav"
            return {"IPX-001"}

        async def get_metadata(self, key, ttl):
            return None

        async def put_metadata(self, key, payload):
            return None

    monkeypatch.setattr("app.batch.search_magnets", fake_search)
    monkeypatch.setattr("app.batch.fetch_metadata", fake_meta)

    async def run():
        return await preview_batch(
            Settings(),
            "SSIS-001\nMIDV-001\nABCD-001 IPX-001",
            library=Lib(),
            db=Db(),
        )

    rows = asyncio.run(run())
    assert [row["code"] for row in rows] == ["SSIS-001", "MIDV-001", "ABCD-001", "IPX-001"]
    assert rows[0]["item"]["info_hash"] == "a" * 40
    assert rows[0]["item"]["title"] == "SSIS-001-UC"
    assert rows[0]["error"] is None
    assert rows[0]["library"]["present"] is True
    assert rows[0]["suck"] is False
    assert rows[0]["metadata"]["title"] == "SSIS meta"
    assert rows[0]["metadata"]["cover"] == "https://example.com/ssis.jpg"
    assert rows[0]["metadata"]["actors"] == [{"name": "葵つかさ"}, {"name": "Extra"}]
    assert "samples" not in rows[0]["metadata"]
    assert rows[1]["item"] is None
    assert rows[1]["error"] == "没有磁链"
    assert rows[1]["meta_error"] == "未找到该番号"
    assert rows[1]["library"]["present"] is False
    assert rows[2]["item"] is None
    assert rows[2]["error"] == "磁力猫请求失败"
    assert rows[3]["item"]["info_hash"] == "c" * 40
    assert rows[3]["suck"] is True
    assert rows[3]["library"]["present"] is False


def test_preview_rejects_empty_and_too_many():
    async def run():
        with pytest.raises(ValueError, match="没有识别到番号"):
            await preview_batch(Settings(), "hello")
        text = "\n".join(f"ABP-{i:03d}" for i in range(1, 42))
        with pytest.raises(ValueError, match="一次最多"):
            await preview_batch(Settings(), text)

    asyncio.run(run())


def test_enqueue_skips_one_failure_and_keeps_going():
    seen = []

    class Jobs:
        async def enqueue_filtered(self, code, info_hash, title):
            seen.append(code)
            if code == "MIDV-001":
                raise Aria2Error("连不上 aria2")
            return {"id": code.lower(), "code": code, "info_hash": info_hash}

    async def run():
        return await enqueue_batch(Jobs(), [
            {"code": "SSIS-001", "info_hash": "a" * 40, "title": "one"},
            {"code": "not a code", "info_hash": "b" * 40, "title": ""},
            {"code": "MIDV-001", "info_hash": "c" * 40, "title": "two"},
            {"code": "IPX-001", "info_hash": "", "title": "three"},
            {"code": "ABP-001", "info_hash": "d" * 40, "title": "four"},
        ])

    result = asyncio.run(run())
    assert [job["code"] for job in result["queued"]] == ["SSIS-001", "ABP-001"]
    assert seen == ["SSIS-001", "MIDV-001", "ABP-001"]
    assert result["skipped"] == [
        {"code": "not a code", "reason": "番号格式无效"},
        {"code": "MIDV-001", "reason": "连不上 aria2"},
        {"code": "IPX-001", "reason": "没有磁链"},
    ]


def test_batch_routes_preview_then_enqueue(monkeypatch):
    async def fake_search(settings, code, pages=2):
        if code == "SSIS-001":
            return [{
                "info_hash": "a" * 40,
                "title": "SSIS-001-UC",
                "heat": 2,
                "size": "2GB",
                "tags": ["UC"],
            }]
        return []

    async def fake_meta(settings, code):
        return {
            "title": f"{code} title",
            "cover": "https://example.com/c.jpg",
            "actors": [{"name": "Actor"}],
        }

    queued = []

    class Jobs:
        async def enqueue_filtered(self, code, info_hash, title):
            queued.append((code, info_hash, title))
            return {"id": "job1", "code": code, "info_hash": info_hash, "title": title}

    monkeypatch.setattr("app.batch.search_magnets", fake_search)
    monkeypatch.setattr("app.batch.fetch_metadata", fake_meta)
    app = FastAPI()
    app.include_router(downloads.router)
    app.state.settings = Settings()
    app.state.jobs = Jobs()
    app.state.library = None
    app.state.db = None

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            preview = await client.post(
                "/api/downloads/batch/preview",
                json=BatchText(text="SSIS-001\nZZZZ-999").model_dump(),
            )
            assert preview.status_code == 200
            body = preview.json()
            assert body["items"][0]["item"]["title"] == "SSIS-001-UC"
            assert body["items"][0]["metadata"]["title"] == "SSIS-001 title"
            assert body["items"][0]["library"]["present"] is False
            assert body["items"][0]["suck"] is False
            assert body["items"][1]["error"] == "没有磁链"
            empty = await client.post("/api/downloads/batch", json={"items": []})
            assert empty.status_code == 400
            confirmed = await client.post(
                "/api/downloads/batch",
                json=BatchEnqueue(items=[{
                    "code": "SSIS-001",
                    "info_hash": "a" * 40,
                    "title": "SSIS-001-UC",
                }]).model_dump(),
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["queued"][0]["code"] == "SSIS-001"
            assert queued == [("SSIS-001", "a" * 40, "SSIS-001-UC")]

    asyncio.run(run())

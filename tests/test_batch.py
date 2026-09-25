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


def test_parse_batch_codes_keeps_paste_order_and_skips_junk():
    text = "ssis001, MIDV-123\nHD-1080  ipx_001\n再看 ABP-123 和 ssis-001"
    assert parse_batch_codes(text) == ["SSIS-001", "MIDV-123", "IPX-001", "ABP-123"]
    assert parse_batch_codes("没有番号") == []
    assert parse_batch_codes("") == []


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

    monkeypatch.setattr("app.batch.search_magnets", fake_search)

    async def run():
        return await preview_batch(
            Settings(),
            "SSIS-001\nMIDV-001\nABCD-001 IPX-001",
        )

    rows = asyncio.run(run())
    assert [row["code"] for row in rows] == ["SSIS-001", "MIDV-001", "ABCD-001", "IPX-001"]
    assert rows[0]["item"]["info_hash"] == "a" * 40
    assert rows[0]["item"]["title"] == "SSIS-001-UC"
    assert rows[0]["error"] is None
    assert rows[1] == {"code": "MIDV-001", "item": None, "error": "没有磁链"}
    assert rows[2]["item"] is None
    assert rows[2]["error"] == "磁力猫请求失败"
    assert rows[3]["item"]["info_hash"] == "c" * 40


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
        async def enqueue(self, code, info_hash, title):
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

    queued = []

    class Jobs:
        async def enqueue(self, code, info_hash, title):
            queued.append((code, info_hash, title))
            return {"id": "job1", "code": code, "info_hash": info_hash, "title": title}

    monkeypatch.setattr("app.batch.search_magnets", fake_search)
    app = FastAPI()
    app.include_router(downloads.router)
    app.state.settings = Settings()
    app.state.jobs = Jobs()

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

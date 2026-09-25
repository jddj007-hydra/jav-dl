import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.sources.clm import (
    MAGNET_CACHE_TTL,
    MagnetSearchError,
    clear_magnet_cache,
    parse_search_html,
    search_magnets,
)
from app.western_magnets import collect_western_magnets

FIXTURE = (Path(__file__).parent / "fixtures" / "clm_search_decoded.html").read_text(encoding="utf-8")


class _Client:
    async def get(self, url, *args, **kwargs):
        return httpx.Response(200, text="ok")


class _Hold:
    async def __aenter__(self):
        return _Client()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _empty_cache():
    clear_magnet_cache()
    yield
    clear_magnet_cache()


def _settings(**kwargs) -> Settings:
    base = dict(
        clm_search="https://primary.test",
        clm_home="https://home.test",
        clm_search_backup="",
    )
    base.update(kwargs)
    return Settings(**base)


def _patch_fetch(monkeypatch, fetch):
    monkeypatch.setattr("app.sources.clm.site_client", lambda settings: _Hold())
    monkeypatch.setattr("app.sources.clm.fetch_search_page", fetch)


def test_repeat_code_and_western_terms_skip_the_network(monkeypatch):
    calls: list[str] = []

    async def fetch(client, url, hops=0):
        calls.append(url)
        return FIXTURE

    _patch_fetch(monkeypatch, fetch)
    settings = _settings()

    async def run():
        first = await search_magnets(settings, "SSIS-001", pages=1)
        second = await search_magnets(settings, "ssis-001", pages=1)
        western = await collect_western_magnets(
            settings, "Vixen", "Something", [], "2024-03-02"
        )
        before = len(calls)
        again = await collect_western_magnets(
            settings, "Vixen", "Something", [], "2024-03-02"
        )
        return first, second, western, before, again, len(calls)

    first, second, western, before, again, after = asyncio.run(run())
    order = [item["info_hash"] for item in parse_search_html(FIXTURE, "SSIS-001")]
    assert [item["info_hash"] for item in first] == order
    assert [item["info_hash"] for item in second] == order
    assert calls[0] == "https://primary.test/search?word=U1NJUy0wMDE=&sort=hits"
    assert before > 1
    assert after == before
    assert again == western
    first[0]["title"] = "mutated"
    async def third():
        return await search_magnets(settings, "SSIS-001", pages=1)

    cached = asyncio.run(third())
    assert cached[0]["title"] != "mutated"


def test_backup_is_used_only_when_primary_fails_or_is_empty(monkeypatch):
    calls: list[str] = []
    mode = {"primary": "ok"}

    async def fetch(client, url, hops=0):
        calls.append(url)
        if "primary.test" in url:
            if mode["primary"] == "down":
                raise httpx.ConnectError("down")
            if mode["primary"] == "empty":
                return "<html></html>"
            if "p=2" in url:
                raise httpx.ConnectError("page2")
            return FIXTURE
        if "backup.test" in url or "home.test" in url:
            return FIXTURE
        raise AssertionError(url)

    _patch_fetch(monkeypatch, fetch)
    settings = _settings(clm_search_backup="https://backup.test")

    async def once(code, **kwargs):
        clear_magnet_cache()
        calls.clear()
        return await search_magnets(settings, code, **kwargs)

    async def run():
        listed = await once("SSIS-001", pages=2)
        primary_calls = list(calls)
        mode["primary"] = "empty"
        from_backup = await once("SSIS-002", pages=1)
        backup_calls = list(calls)
        mode["primary"] = "down"
        from_error = await once("SSIS-003", pages=1)
        error_calls = list(calls)
        return listed, primary_calls, from_backup, backup_calls, from_error, error_calls

    listed, primary_calls, from_backup, backup_calls, from_error, error_calls = asyncio.run(run())
    assert listed
    assert all("primary.test" in url for url in primary_calls)
    assert not any("backup.test" in url for url in primary_calls)
    assert any("backup.test" in url for url in backup_calls)
    assert from_backup
    assert any("backup.test" in url for url in error_calls)
    assert from_error
    assert [item["info_hash"] for item in from_backup] == [
        item["info_hash"] for item in parse_search_html(FIXTURE, "SSIS-002")
    ]


def test_home_covers_a_dead_primary_and_errors_are_not_cached(monkeypatch):
    calls: list[str] = []

    async def fetch(client, url, hops=0):
        calls.append(url)
        if "primary.test" in url:
            raise httpx.ConnectError("down")
        if "home.test" in url:
            return FIXTURE
        raise AssertionError(url)

    _patch_fetch(monkeypatch, fetch)

    async def run():
        found = await search_magnets(_settings(), "SSIS-001", pages=1)
        before = len(calls)
        again = await search_magnets(_settings(), "SSIS-001", pages=1)
        return found, before, again, len(calls)

    found, before, again, after = asyncio.run(run())
    assert found
    assert any("home.test/search" in url for url in calls)
    assert after == before
    assert [item["info_hash"] for item in again] == [item["info_hash"] for item in found]

    calls.clear()
    clear_magnet_cache()

    async def fail(client, url, hops=0):
        calls.append(url)
        raise httpx.ConnectError("down")

    _patch_fetch(monkeypatch, fail)

    async def boom():
        with pytest.raises(MagnetSearchError):
            await search_magnets(_settings(), "MISS-001", pages=1)
        first = len(calls)
        with pytest.raises(MagnetSearchError):
            await search_magnets(_settings(), "MISS-001", pages=1)
        return first, len(calls)

    first, second = asyncio.run(boom())
    assert second > first


def test_empty_results_stay_cached_until_they_expire(monkeypatch):
    calls: list[str] = []
    clock = {"now": 1000.0}

    async def fetch(client, url, hops=0):
        calls.append(url)
        return "<html></html>"

    _patch_fetch(monkeypatch, fetch)
    monkeypatch.setattr("app.sources.clm.time.monotonic", lambda: clock["now"])
    settings = _settings(clm_home="https://primary.test")

    async def run():
        empty = await search_magnets(settings, "NONE-001", pages=1)
        after_empty = len(calls)
        await search_magnets(settings, "NONE-001", pages=1)
        return empty, after_empty, len(calls)

    empty, after_empty, again = asyncio.run(run())
    assert empty == []
    assert again == after_empty

    calls.clear()

    async def hit(client, url, hops=0):
        calls.append(url)
        return FIXTURE

    _patch_fetch(monkeypatch, hit)

    async def timed():
        await search_magnets(settings, "SSIS-001", pages=1)
        clock["now"] += MAGNET_CACHE_TTL + 1
        await search_magnets(settings, "SSIS-001", pages=1)
        return len(calls)

    assert asyncio.run(timed()) == 2

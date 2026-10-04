"""Paste several JAV codes, preview the magnet each would queue, then enqueue."""

from __future__ import annotations

import asyncio
import re

from app.codes import (
    CODE_HYPHEN_RE,
    CODE_LOOSE_RE,
    DATE_HYPHEN_RE,
    FALSE_PREFIXES,
    FC2_HYPHEN_RE,
    normalize_code,
)
from app.config import Settings
from app.downloader.jobs import BackendError
from app.library import library_info
from app.ranking import sort_resources
from app.sources.clm import MagnetSearchError, search_magnets
from app.sources.javbus import CACHE_VER, MetadataError, fetch_metadata

BATCH_LIMIT = 40
_PREVIEW_CONCURRENCY = 5
_SPLIT = re.compile(r"[\s,，;；、|]+")
_WRAP = "[]()（）\"'“”`<>《》"


def parse_batch_codes(text: str) -> list[str]:
    """Pull unique codes out of a pasted list. Order follows the paste."""
    found: list[str] = []
    seen: set[str] = set()

    def add(code: str | None) -> None:
        if not code or code in seen:
            return
        prefix = code.split("-", 1)[0]
        if prefix in FALSE_PREFIXES:
            return
        seen.add(code)
        found.append(code)

    raw = text or ""
    for token in _SPLIT.split(raw):
        add(normalize_code(token.strip().strip(_WRAP)))
    folded = raw.upper().replace("_", "-")
    # FC2-PPV-4587943 里的 PPV-45879 会被普通厂牌正则误伤，先记 FC2 再跳过重叠段。
    fc2_spans: list[tuple[int, int]] = []
    for match in FC2_HYPHEN_RE.finditer(folded):
        add(normalize_code(f"FC2-{match.group(1)}"))
        fc2_spans.append(match.span())
    for match in DATE_HYPHEN_RE.finditer(folded):
        add(f"{match.group(1)}-{match.group(2)}")
    for match in CODE_HYPHEN_RE.finditer(folded):
        start, end = match.span()
        if any(span_start < end and span_end > start for span_start, span_end in fc2_spans):
            continue
        prefix, num = match.group(1), match.group(2)
        if prefix not in FALSE_PREFIXES:
            add(f"{prefix}-{num}")
    for token in _SPLIT.split(folded):
        compact = re.sub(r"[^A-Z0-9]", "", token)
        if not compact:
            continue
        add(normalize_code(compact))
        match = CODE_LOOSE_RE.fullmatch(compact)
        if not match:
            continue
        prefix, num = match.group(1), match.group(2)
        if prefix not in FALSE_PREFIXES:
            add(f"{prefix}-{num}")
    return found


def _picked(item: dict) -> dict:
    return {
        "info_hash": item.get("info_hash") or "",
        "title": item.get("title") or "",
        "size": item.get("size") or "",
        "size_bytes": item.get("size_bytes"),
        "heat": int(item.get("heat") or 0),
        "tags": list(item.get("tags") or []),
        "rank": int(item.get("rank") or 1),
    }


def _slim_meta(meta: dict | None) -> dict | None:
    if not meta:
        return None
    actors = []
    for actor in meta.get("actors") or []:
        if isinstance(actor, dict):
            name = (actor.get("name") or "").strip()
        else:
            name = str(actor or "").strip()
        if name:
            actors.append({"name": name})
        if len(actors) >= 8:
            break
    return {
        "title": meta.get("title") or "",
        "cover": meta.get("cover") or "",
        "release_date": meta.get("release_date") or "",
        "runtime": meta.get("runtime") or "",
        "studio": meta.get("studio") or "",
        "actors": actors,
    }


async def _magnet_for(settings: Settings, code: str) -> tuple[dict | None, str | None]:
    try:
        magnets = await search_magnets(settings, code)
    except MagnetSearchError as exc:
        return None, str(exc)
    ranked = sort_resources(magnets, code)
    if not ranked:
        return None, "没有磁链"
    return _picked(ranked[0]), None


async def _meta_for(settings: Settings, db, code: str) -> tuple[dict | None, str | None]:
    cache_key = f"{CACHE_VER}:{code}"
    if db is not None:
        cached = await db.get_metadata(cache_key, settings.metadata_ttl)
        if cached:
            return _slim_meta(cached), None
    try:
        meta = await fetch_metadata(settings, code)
    except MetadataError as exc:
        return None, str(exc)
    if db is not None:
        await db.put_metadata(cache_key, meta)
    return _slim_meta(meta), None


async def preview_batch(
    settings: Settings,
    text: str,
    *,
    library=None,
    db=None,
) -> list[dict]:
    codes = parse_batch_codes(text)
    if not codes:
        raise ValueError("没有识别到番号")
    if len(codes) > BATCH_LIMIT:
        raise ValueError(f"一次最多 {BATCH_LIMIT} 个番号")

    hits: dict[str, dict] = {}
    suck: set[str] = set()
    if library is not None:
        hits = await library.get_many(codes)
    if db is not None and hasattr(db, "suck_keys"):
        suck = await db.suck_keys("jav", codes)

    sem = asyncio.Semaphore(_PREVIEW_CONCURRENCY)

    async def one(code: str) -> dict:
        async with sem:
            (item, error), (meta, meta_error) = await asyncio.gather(
                _magnet_for(settings, code),
                _meta_for(settings, db, code),
            )
        return {
            "code": code,
            "item": item,
            "error": error,
            "metadata": meta,
            "meta_error": meta_error,
            "library": library_info(hits.get(code)),
            "suck": code in suck,
        }

    return list(await asyncio.gather(*(one(code) for code in codes)))


async def enqueue_batch(jobs, rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("没有可入队的番号")
    if len(rows) > BATCH_LIMIT:
        raise ValueError(f"一次最多 {BATCH_LIMIT} 个番号")
    queued: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        raw_code = str((row or {}).get("code") or "")
        code = normalize_code(raw_code)
        info_hash = str((row or {}).get("info_hash") or "").strip().lower()
        title = str((row or {}).get("title") or "")
        if not code:
            skipped.append({"code": raw_code, "reason": "番号格式无效"})
            continue
        if len(info_hash) != 40 or any(ch not in "0123456789abcdef" for ch in info_hash):
            skipped.append({"code": code, "reason": "没有磁链"})
            continue
        db = getattr(jobs, "db", None)
        if db is not None and await db.is_suck("jav", code):
            skipped.append({"code": code, "reason": "已标 suck"})
            continue
        try:
            job = await jobs.enqueue_filtered(code, info_hash, title)
        except BackendError as exc:
            skipped.append({"code": code, "reason": str(exc)})
            continue
        queued.append(job)
    return {"queued": queued, "skipped": skipped}

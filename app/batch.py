"""Paste several JAV codes, preview the magnet each would queue, then enqueue."""

from __future__ import annotations

import re

from app.codes import CODE_HYPHEN_RE, CODE_LOOSE_RE, FALSE_PREFIXES, normalize_code
from app.config import Settings
from app.downloader.jobs import BackendError
from app.ranking import sort_resources
from app.sources.clm import MagnetSearchError, search_magnets

BATCH_LIMIT = 40
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
    for match in CODE_HYPHEN_RE.finditer(folded):
        prefix, num = match.group(1), match.group(2)
        if prefix not in FALSE_PREFIXES:
            add(f"{prefix}-{num}")
    for token in _SPLIT.split(folded):
        compact = re.sub(r"[^A-Z0-9]", "", token)
        match = CODE_LOOSE_RE.fullmatch(compact) if compact else None
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


async def preview_batch(settings: Settings, text: str) -> list[dict]:
    codes = parse_batch_codes(text)
    if not codes:
        raise ValueError("没有识别到番号")
    if len(codes) > BATCH_LIMIT:
        raise ValueError(f"一次最多 {BATCH_LIMIT} 个番号")
    rows: list[dict] = []
    for code in codes:
        try:
            magnets = await search_magnets(settings, code)
        except MagnetSearchError as exc:
            rows.append({"code": code, "item": None, "error": str(exc)})
            continue
        ranked = sort_resources(magnets, code)
        if not ranked:
            rows.append({"code": code, "item": None, "error": "没有磁链"})
            continue
        rows.append({"code": code, "item": _picked(ranked[0]), "error": None})
    return rows


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
        try:
            job = await jobs.enqueue(code, info_hash, title)
        except BackendError as exc:
            skipped.append({"code": code, "reason": str(exc)})
            continue
        queued.append(job)
    return {"queued": queued, "skipped": skipped}

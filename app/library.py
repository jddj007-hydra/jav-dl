from __future__ import annotations

import asyncio
from pathlib import Path

from app.codes import normalize_code
from app.config import Settings
from app.db import Database
from app.scrape import is_video

POSTER_NAMES = {"poster.jpg", "poster.png", "poster.jpeg"}


def library_info(hit: dict | None) -> dict:
    if not hit or not hit.get("has_video"):
        return {"present": False}
    return {
        "present": True,
        "path": hit.get("path") or "",
        "has_nfo": bool(hit.get("has_nfo")),
        "has_poster": bool(hit.get("has_poster")),
    }


def attach_library(items: list[dict], hits: dict[str, dict]) -> list[dict]:
    out = []
    for item in items:
        row = dict(item)
        code = row.get("code") or ""
        key = normalize_code(code) or code.strip().upper()
        row["library"] = library_info(hits.get(key))
        out.append(row)
    return out


def scan_media(media_dir: Path) -> list[dict]:
    if not media_dir or not media_dir.is_dir():
        return []
    found: dict[str, dict] = {}
    for month_dir in sorted(media_dir.iterdir()):
        if not month_dir.is_dir():
            continue
        for code_dir in sorted(month_dir.iterdir()):
            if not code_dir.is_dir():
                continue
            code = normalize_code(code_dir.name) or code_dir.name.strip().upper()
            if not code:
                continue
            has_video = False
            has_nfo = False
            has_poster = False
            try:
                files = list(code_dir.iterdir())
            except OSError:
                continue
            for path in files:
                if not path.is_file():
                    continue
                name = path.name.lower()
                if is_video(path):
                    has_video = True
                elif path.suffix.lower() == ".nfo":
                    has_nfo = True
                elif name in POSTER_NAMES:
                    has_poster = True
            if not has_video:
                continue
            rel = f"{month_dir.name}/{code_dir.name}"
            prev = found.get(code)
            if prev and prev["month"] > month_dir.name:
                continue
            found[code] = {
                "code": code,
                "month": month_dir.name,
                "path": rel,
                "has_video": 1,
                "has_nfo": 1 if has_nfo else 0,
                "has_poster": 1 if has_poster else 0,
            }
    return list(found.values())


class Library:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._lock = asyncio.Lock()

    async def refresh(self) -> int:
        async with self._lock:
            rows = await asyncio.to_thread(scan_media, self.settings.media_dir)
            await self.db.replace_library(rows)
            return len(rows)

    async def upsert(self, row: dict) -> None:
        await self.db.upsert_library(row)

    async def get(self, code: str) -> dict | None:
        key = normalize_code(code) or (code or "").strip().upper()
        if not key:
            return None
        return await self.db.get_library(key)

    async def get_many(self, codes: list[str]) -> dict[str, dict]:
        keys = []
        for code in codes:
            key = normalize_code(code) or (code or "").strip().upper()
            if key:
                keys.append(key)
        if not keys:
            return {}
        return await self.db.get_library_many(keys)

    async def info_for(self, code: str) -> dict:
        return library_info(await self.get(code))

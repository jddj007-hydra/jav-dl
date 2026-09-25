from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
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


def tpdb_id_from_nfo(text: str) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""
    for el in root.iter("uniqueid"):
        if (el.attrib.get("type") or "").lower() != "tpdb":
            continue
        value = (el.text or "").strip()
        if value:
            return value
    return ""


def nfo_title(text: str) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""
    for tag in ("originaltitle", "title"):
        value = (root.findtext(tag) or "").strip()
        if value:
            return value
    return ""


def attach_western(items: list[dict], hits: dict[str, dict]) -> list[dict]:
    out = []
    for item in items:
        row = dict(item)
        hit = hits.get(str(row.get("id") or ""))
        if hit is not None:
            hit = {**hit, "has_video": 1}
        row["library"] = library_info(hit)
        out.append(row)
    return out


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


def scan_western(root: Path | None) -> list[dict]:
    if root is None or not root.is_dir():
        return []
    rows: list[dict] = []
    for studio in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        try:
            files = [path for path in studio.iterdir() if path.is_file()]
        except OSError:
            continue
        by_name = {path.name.lower(): path for path in files}
        for video in sorted(path for path in files if is_video(path)):
            nfo = by_name.get(f"{video.stem.lower()}.nfo")
            poster = by_name.get(f"{video.stem.lower()}-poster.jpg")
            text = ""
            if nfo:
                try:
                    text = nfo.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
            title = nfo_title(text) or video.stem
            rows.append({
                "path": f"{studio.name}/{video.name}",
                "tpdb_id": tpdb_id_from_nfo(text),
                "studio": studio.name,
                "title": title,
                "has_nfo": 1 if nfo else 0,
                "has_poster": 1 if poster else 0,
            })
    return rows


class Library:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._lock = asyncio.Lock()

    async def refresh(self) -> int:
        async with self._lock:
            rows = await asyncio.to_thread(scan_media, self.settings.media_dir)
            western = await asyncio.to_thread(scan_western, self.settings.western_root)
            await self.db.replace_library(rows)
            await self.db.replace_western(western)
            return len(rows) + len(western)

    async def remember_western(self, result: dict) -> None:
        for entry in result.get("entries") or []:
            if entry.get("path"):
                await self.db.upsert_western(entry)

    async def western_many(self, ids: list[str]) -> dict[str, dict]:
        return await self.db.western_by_ids(ids)

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

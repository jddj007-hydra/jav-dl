from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

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
    return nfo_fields(text)["title"]


def nfo_fields(text: str) -> dict:
    empty = {"title": "", "actors": [], "release_date": ""}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return empty
    title = ""
    for tag in ("originaltitle", "title"):
        value = (root.findtext(tag) or "").strip()
        if value:
            title = value
            break
    actors = []
    for el in root.iter("actor"):
        name = (el.findtext("name") or "").strip()
        if name and name not in actors:
            actors.append(name)
    release = ""
    for tag in ("premiered", "releasedate"):
        value = (root.findtext(tag) or "").strip()
        if value:
            release = value
            break
    return {"title": title, "actors": actors, "release_date": release}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _safe_join(root: Path | None, rel: str) -> Path | None:
    if root is None or not rel or "\\" in rel or "\x00" in rel:
        return None
    parts = PurePosixPath(rel).parts
    if not parts or PurePosixPath(rel).is_absolute() or any(p in ("", ".", "..") for p in parts):
        return None
    return root.joinpath(*parts)


def poster_file(root: Path | None, rel: str, kind: str) -> Path | None:
    target = _safe_join(root, rel)
    if target is None:
        return None
    if kind == "western":
        candidates = [target.with_name(f"{target.stem}-poster.jpg")]
    else:
        candidates = [target / name for name in ("poster.jpg", "poster.png", "poster.jpeg")]
    for path in candidates:
        if path.is_file():
            return path
    return None


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
            added_at = 0.0
            nfo: Path | None = None
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
                    added_at = max(added_at, _mtime(path) or 1.0)
                elif path.suffix.lower() == ".nfo":
                    nfo = nfo or path
                elif name in POSTER_NAMES:
                    has_poster = True
            if not added_at:
                continue
            rel = f"{month_dir.name}/{code_dir.name}"
            prev = found.get(code)
            if prev and prev["month"] > month_dir.name:
                continue
            fields = nfo_fields(_read_text(nfo)) if nfo else {}
            found[code] = {
                "code": code,
                "month": month_dir.name,
                "path": rel,
                "has_video": 1,
                "has_nfo": 1 if nfo else 0,
                "has_poster": 1 if has_poster else 0,
                "title": fields.get("title") or "",
                "actors": fields.get("actors") or [],
                "release_date": fields.get("release_date") or "",
                "added_at": added_at,
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
            text = _read_text(nfo)
            fields = nfo_fields(text)
            rows.append({
                "path": f"{studio.name}/{video.name}",
                "tpdb_id": tpdb_id_from_nfo(text),
                "studio": studio.name,
                "title": fields["title"] or video.stem,
                "has_nfo": 1 if nfo else 0,
                "has_poster": 1 if poster else 0,
                "actors": fields["actors"],
                "release_date": fields["release_date"],
                "added_at": _mtime(video),
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

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from app.codes import jav_vr_maker, normalize_code
from app.config import Settings
from app.db import Database
from app.scrape import is_video
from app.western_archive import western_fs_rel

log = logging.getLogger("app.library")

POSTER_NAMES = ("poster.jpg", "poster.png", "poster.jpeg", "folder.jpg", "cover.jpg", "fanart.jpg")
WESTERN_POSTER_SUFFIXES = ("-poster.jpg", ".jpg", "-poster.png")


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
    empty = {
        "title": "",
        "actors": [],
        "release_date": "",
        "studio": "",
        "genres": [],
        "outline": "",
        "runtime_min": 0,
        "series": "",
    }
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
    genres = []
    for tag in ("genre", "tag"):
        for el in root.iter(tag):
            name = (el.text or "").strip()
            if name and name not in genres:
                genres.append(name)
    outline = (root.findtext("outline") or root.findtext("plot") or "").strip()
    if len(outline) > 8000:
        outline = outline[:8000]
    return {
        "title": title,
        "actors": actors,
        "release_date": release,
        "studio": (root.findtext("studio") or root.findtext("maker") or "").strip(),
        "genres": genres,
        "outline": outline,
        "runtime_min": runtime_minutes(root.findtext("runtime")),
        "series": _series_name(root),
    }


def _series_name(root) -> str:
    for element in root.iter("set"):
        name = (element.findtext("name") or "").strip()
        if name:
            return name[:200]
        text = (element.text or "").strip()
        if text:
            return text[:200]
    return ((root.findtext("series") or "").strip())[:200]


def runtime_minutes(value: object) -> int:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        return 0
    minutes = int(match.group(1))
    if minutes <= 0 or minutes >= 1000:
        return 0
    return minutes


def year_of(release: str) -> int:
    text = (release or "")[:4]
    if len(text) < 4 or not text.isdigit():
        return 0
    year = int(text)
    if year < 1900 or year > 2100:
        return 0
    return year


_RES_RE = re.compile(r"(?i)(?:^|[^0-9])(2160|1440|1080|720|480|360)p(?:[^a-z0-9]|$)")
_FOUR_K_RE = re.compile(r"(?i)(?:^|[^a-z0-9])4k(?:[^a-z0-9]|$)")


def resolution_of(name: str) -> str:
    match = _RES_RE.search(name or "")
    if match:
        return f"{match.group(1)}p"
    if _FOUR_K_RE.search(name or ""):
        return "4k"
    return ""


def release_flags(names: list[str], genres: list[str]) -> dict:
    has_sub = False
    uncensored = False
    cracked = False
    for name in names:
        stem = Path(name).stem
        low = stem.lower()
        if low.endswith("-uc") or low.endswith("-c"):
            has_sub = True
        if (
            "破解" in stem
            or "uncensored" in low
            or "流出" in stem
            or low.endswith("-uc")
            or low.endswith("-u")
        ):
            cracked = True
    for genre in genres:
        if "中文字幕" in genre or genre == "字幕":
            has_sub = True
        if "无码" in genre or "無碼" in genre:
            uncensored = True
        if "破解" in genre or "流出" in genre:
            cracked = True
    return {
        "has_sub": 1 if has_sub else 0,
        "has_uncensored": 1 if uncensored else 0,
        "has_cracked": 1 if cracked else 0,
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _pick_nfo(files: list[Path], code: str) -> Path | None:
    nfos = [path for path in files if path.is_file() and path.suffix.lower() == ".nfo"]
    exact = None
    sub = None
    wanted = code.lower()
    for path in nfos:
        stem = path.stem.lower()
        if stem == wanted:
            exact = path
        elif stem == f"{wanted}-c":
            sub = path
    if exact is not None:
        return exact
    if sub is not None:
        return sub
    if not nfos:
        return None
    return max(nfos, key=_file_size)


def _pick_jav_poster(files: list[Path]) -> Path | None:
    by_name = {path.name.lower(): path for path in files if path.is_file()}
    for name in POSTER_NAMES:
        found = by_name.get(name)
        if found is not None:
            return found
    return None


def _pick_western_nfo(by_name: dict[str, Path], stem: str) -> Path | None:
    return by_name.get(f"{stem}.nfo") or by_name.get("movie.nfo")


def _pick_western_poster(by_name: dict[str, Path], stem: str) -> Path | None:
    for suffix in WESTERN_POSTER_SUFFIXES:
        found = by_name.get(f"{stem}{suffix}")
        if found is not None:
            return found
    for name in POSTER_NAMES:
        found = by_name.get(name)
        if found is not None:
            return found
    return None


def index_code_dir(code_dir: Path, month: str) -> dict | None:
    code = normalize_code(code_dir.name) or code_dir.name.strip().upper()
    if not code:
        return None
    try:
        files = [path for path in code_dir.iterdir() if path.is_file()]
    except OSError:
        return None
    videos = [path for path in files if is_video(path)]
    if not videos:
        return None
    mains = [path for path in videos if "sample" not in path.stem.lower()]
    pool = mains or videos
    main = max(pool, key=_file_size)
    nfo = _pick_nfo(files, code)
    poster = _pick_jav_poster(files)
    fields = nfo_fields(_read_text(nfo)) if nfo else nfo_fields("")
    rel = f"{month}/{code_dir.name}"
    flags = release_flags(
        [code_dir.name, main.name, nfo.name if nfo else ""],
        fields["genres"],
    )
    return {
        "code": code,
        "month": month,
        "path": rel,
        "has_video": 1,
        "has_nfo": 1 if nfo else 0,
        "has_poster": 1 if poster else 0,
        "title": fields["title"],
        "actors": fields["actors"],
        "release_date": fields["release_date"],
        "added_at": max(_mtime(path) or 1.0 for path in videos),
        "studio": fields["studio"],
        "genres": fields["genres"],
        "outline": fields["outline"],
        "runtime_min": fields["runtime_min"],
        "video": f"{rel}/{main.name}",
        "video_count": len(pool),
        "video_size": _file_size(main),
        "poster": f"{rel}/{poster.name}" if poster else "",
        "series": fields["series"],
        **flags,
    }


def western_catalog_fields(path: str, release_date: str, runtime: object, poster_rel: str = "") -> dict:
    pure = PurePosixPath(path)
    return {
        "runtime_min": runtime_minutes(runtime),
        "year": year_of(release_date),
        "resolution": resolution_of(pure.name),
        "poster": poster_rel,
    }


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
    if kind in ("western", "vr"):
        rel = western_fs_rel(rel)
    target = _safe_join(root, rel)
    if target is None:
        return None
    if kind in ("western", "vr"):
        parent = target.parent
        stem = target.stem.lower()
        candidates = [parent / f"{target.stem}{suffix}" for suffix in WESTERN_POSTER_SUFFIXES]
        candidates.extend(parent / name for name in POSTER_NAMES)
    else:
        candidates = [target / name for name in POSTER_NAMES]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return path
    if kind in ("western", "vr"):
        try:
            names = list(target.parent.iterdir()) if target.parent.is_dir() else []
        except OSError:
            names = []
        for path in names:
            low = path.name.lower()
            if path.is_file() and (low == f"{stem}-poster.jpg" or low == f"{stem}.jpg"):
                return path
    return None


def attach_western(items: list[dict], hits: dict[str, dict], suck: set[str] | None = None) -> list[dict]:
    marked = suck or set()
    out = []
    for item in items:
        row = dict(item)
        item_id = str(row.get("id") or "")
        hit = hits.get(item_id)
        if hit is not None:
            hit = {**hit, "has_video": 1}
        row["library"] = library_info(hit)
        row["suck"] = item_id in marked
        out.append(row)
    return out


def item_code(code: str) -> str:
    return normalize_code(code or "") or (code or "").strip().upper()


def attach_library(items: list[dict], hits: dict[str, dict], suck: set[str] | None = None) -> list[dict]:
    marked = suck or set()
    out = []
    for item in items:
        row = dict(item)
        key = item_code(row.get("code") or "")
        row["library"] = library_info(hits.get(key))
        row["suck"] = bool(key) and key in marked
        out.append(row)
    return out


def _inside(root: Path, path: Path) -> Path | None:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return None
    if resolved == base or not resolved.is_relative_to(base):
        return None
    return resolved


def _drop_empty_dir(directory: Path, root: Path) -> None:
    if directory == root or directory.parent != root:
        return
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        return


def remove_archived(root: Path | None, rel: str, kind: str) -> bool:
    """Delete one archived work. The path has to stay inside that library root."""
    if kind in ("western", "vr"):
        rel = western_fs_rel(rel)
    target = _safe_join(root, rel)
    if target is None or root is None:
        return False
    resolved = _inside(root, target)
    if resolved is None:
        return False
    if kind in ("western", "vr"):
        if not resolved.is_file():
            return False
        parent = resolved.parent
        stem = resolved.stem
        resolved.unlink()
        for name in (f"{stem}.nfo", f"{stem}-poster.jpg"):
            extra = parent / name
            kept = _inside(root, extra)
            if kept is not None and kept.is_file() and kept.parent == parent:
                kept.unlink()
        _drop_empty_dir(parent, root.resolve())
        return True
    if not resolved.is_dir() or resolved.parent.parent != root.resolve():
        return False
    month = resolved.parent
    shutil.rmtree(resolved)
    _drop_empty_dir(month, root.resolve())
    return True


def library_target(kind: str, key: str) -> tuple[str, str]:
    kind = (kind or "").strip().lower()
    if kind not in ("jav", "western"):
        raise ValueError("类型无效")
    raw = (key or "").strip()
    if kind == "jav":
        code = normalize_code(raw)
        if not code:
            raise ValueError("番号格式无效")
        return kind, code
    if not raw or any(ch in raw for ch in "/\\") or len(raw) > 80:
        raise ValueError("缺少作品 id")
    return kind, raw


def _remove_row_files(settings: Settings, kind: str, row: dict) -> bool:
    path = row.get("path") or ""
    if kind == "jav":
        if remove_archived(settings.media_dir, path, "jav"):
            return True
        if settings.jav_vr_root is not None:
            return remove_archived(settings.jav_vr_root, path, "jav")
        return False
    shelf = (row.get("shelf") or "").strip()
    if shelf == "vr" or str(path).startswith("vr/"):
        return remove_archived(settings.vr_root, path, "vr")
    return remove_archived(settings.western_root, path, "western")


async def drop_archived_version(db: Database, settings: Settings, *, kind: str, key: str) -> dict:
    """Delete one archived copy and its library row. Does not mark suck or touch plays."""
    kind, key = library_target(kind, key)
    if kind == "jav":
        row = await db.get_library(key)
    else:
        row = (await db.western_by_ids([key])).get(key)
    if not row or not (row.get("path") or "").strip():
        raise ValueError("库里没有这个版本")
    removed = await asyncio.to_thread(_remove_row_files, settings, kind, row)
    if not removed:
        raise ValueError("文件没有删掉")
    if kind == "jav":
        await db.delete_library(key)
    else:
        await db.delete_western(row["path"])
    return {"kind": kind, "key": key, "removed": True}


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
            row = index_code_dir(code_dir, month_dir.name)
            if row is None:
                continue
            prev = found.get(row["code"])
            if prev and prev["month"] > row["month"]:
                continue
            found[row["code"]] = row
    return list(found.values())


def scan_western(root: Path | None, shelf: str = "western") -> list[dict]:
    if root is None or not root.is_dir():
        return []
    rows: list[dict] = []
    for studio, folder, prefix in _western_folders(root):
        try:
            files = [path for path in folder.iterdir() if path.is_file()]
        except OSError:
            continue
        by_name = {path.name.lower(): path for path in files}
        for video in sorted(path for path in files if is_video(path)):
            nfo = _pick_western_nfo(by_name, video.stem.lower())
            poster = _pick_western_poster(by_name, video.stem.lower())
            text = _read_text(nfo)
            fields = nfo_fields(text)
            rel = f"{prefix}/{video.name}"
            path = rel if shelf != "vr" else f"vr/{rel}"
            poster_rel = ""
            if poster is not None:
                poster_rel = f"{prefix}/{poster.name}"
                if shelf == "vr":
                    poster_rel = f"vr/{poster_rel}"
            rows.append({
                "path": path,
                "tpdb_id": tpdb_id_from_nfo(text),
                "studio": studio,
                "shelf": shelf,
                "title": fields["title"] or video.stem,
                "has_nfo": 1 if nfo else 0,
                "has_poster": 1 if poster else 0,
                "actors": fields["actors"],
                "release_date": fields["release_date"],
                "added_at": _mtime(video),
                **western_catalog_fields(
                    path, fields["release_date"], fields["runtime_min"], poster_rel,
                ),
            })
    return rows


def _western_folders(root: Path) -> list[tuple[str, Path, str]]:
    found: list[tuple[str, Path, str]] = []
    try:
        studios = sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))
    except OSError:
        return found
    for studio in studios:
        found.append((studio.name, studio, studio.name))
        try:
            children = sorted(studio.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and not child.name.startswith("."):
                found.append((studio.name, child, f"{studio.name}/{child.name}"))
    return found


def reindex_western_video(video: Path, studio: str, shelf: str, db_path: str) -> dict | None:
    if not is_video(video):
        return None
    try:
        files = [path for path in video.parent.iterdir() if path.is_file()]
    except OSError:
        return None
    by_name = {path.name.lower(): path for path in files}
    nfo = _pick_western_nfo(by_name, video.stem.lower())
    poster = _pick_western_poster(by_name, video.stem.lower())
    text = _read_text(nfo)
    fields = nfo_fields(text)
    fs_rel = western_fs_rel(db_path)
    prefix = str(PurePosixPath(fs_rel).parent)
    poster_rel = f"{prefix}/{poster.name}" if poster and prefix not in (".", "") else (poster.name if poster else "")
    if shelf == "vr" and poster_rel and not poster_rel.startswith("vr/"):
        poster_rel = f"vr/{poster_rel}"
    return {
        "path": db_path,
        "tpdb_id": tpdb_id_from_nfo(text),
        "studio": studio,
        "shelf": shelf,
        "title": fields["title"] or video.stem,
        "has_nfo": 1 if nfo else 0,
        "has_poster": 1 if poster else 0,
        "actors": fields["actors"],
        "release_date": fields["release_date"],
        "added_at": _mtime(video),
        **western_catalog_fields(db_path, fields["release_date"], fields["runtime_min"], poster_rel),
    }


def archived_jav_path(settings: Settings, rel: str) -> Path:
    if not rel:
        return settings.media_dir
    candidates = [settings.media_dir / rel]
    if settings.jav_vr_root is not None:
        candidates.append(settings.jav_vr_root / rel)
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1] if settings.jav_vr_root is not None else candidates[0]


class Library:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._lock = asyncio.Lock()

    async def refresh(self, scrape_missing: bool = False) -> int:
        async with self._lock:
            rows = await asyncio.to_thread(scan_media, self.settings.media_dir)
            jav_vr = self.settings.jav_vr_root
            if jav_vr is not None and jav_vr != self.settings.media_dir:
                extra = await asyncio.to_thread(scan_media, jav_vr)
                by_code = {row["code"]: row for row in rows}
                for row in extra:
                    by_code[row["code"]] = row
                rows = list(by_code.values())
            western = await asyncio.to_thread(scan_western, self.settings.western_root, "western")
            vr_root = self.settings.vr_root
            if vr_root is not None and vr_root != self.settings.western_root:
                western.extend(await asyncio.to_thread(scan_western, vr_root, "vr"))
            if scrape_missing:
                rows, western = await self._fill_missing(rows, western)
            await self.db.replace_library(rows)
            await self.db.replace_western(western)
            return len(rows) + len(western)

    async def _fill_missing(self, jav_rows: list[dict], western_rows: list[dict]) -> tuple[list[dict], list[dict]]:
        from app.scrape import ScrapeError, fill_jav_folder
        from app.western_archive import fill_western_video

        sem = asyncio.Semaphore(3)

        async def fill_jav(row: dict) -> dict:
            if not jav_vr_maker(row.get("code") or ""):
                return row
            if row.get("has_nfo") and row.get("has_poster"):
                return row
            dest = archived_jav_path(self.settings, row.get("path") or "")
            if not dest.is_dir():
                return row
            async with sem:
                try:
                    updated = await fill_jav_folder(self.settings, self.db, dest, row["code"])
                except (OSError, ScrapeError) as exc:
                    log.warning("番号 VR 补刮失败 %s: %s", row.get("code"), exc)
                    return row
            return updated or row

        async def fill_west(row: dict) -> dict:
            if (row.get("shelf") or "") != "vr" and not str(row.get("path") or "").startswith("vr/"):
                return row
            if row.get("has_nfo") and row.get("has_poster"):
                return row
            root = self.settings.vr_root
            rel = western_fs_rel(row.get("path") or "")
            video = (root / rel) if root and rel else None
            if video is None or not video.is_file():
                return row
            async with sem:
                try:
                    ok = await fill_western_video(self.settings, video)
                except OSError as exc:
                    log.warning("欧美 VR 补刮失败 %s: %s", row.get("path"), exc)
                    return row
            if not ok or video is None:
                return row
            return reindex_western_video(
                video, row.get("studio") or video.parent.name, "vr", row.get("path") or "",
            ) or row

        jav_done, west_done = await asyncio.gather(
            asyncio.gather(*(fill_jav(row) for row in jav_rows)),
            asyncio.gather(*(fill_west(row) for row in western_rows)),
        )
        return list(jav_done), list(west_done)

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

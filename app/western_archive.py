from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

from app.config import Settings
from app.nfo import build_nfo
from app.scrape import (
    ScrapeError,
    VIDEO_EXTS,
    fetch_cover_bytes,
    is_incomplete,
    is_video,
    iter_videos,
    iter_watch_targets,
    safe_rmtree,
    source_incomplete,
    source_mtime,
)
from app.sources.tpdb import TpdbError, fetch_by_filename, fetch_detail
from app.western_magnets import is_western_release_name, release_text

log = logging.getLogger("app.western_archive")

SIDECAR = ".javdl.json"
_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)
_ALNUM = re.compile(r"[^a-z0-9]+")


def write_sidecar(dest: Path, info: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / SIDECAR).write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_sidecar(dest: Path) -> dict | None:
    path = dest / SIDECAR
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _key(name: str) -> str:
    return _ALNUM.sub("", (name or "").lower())


def studio_dir(root: Path, site: str) -> Path:
    label = (site or "").strip() or "Unknown"
    want = _key(label) or "unknown"
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and _key(child.name) == want:
                return child
    cleaned = _UNSAFE.sub("", label).strip() or "Unknown"
    cleaned = re.sub(r"\s+", "", cleaned) or "Unknown"
    return root / cleaned


def _norm_name(name: str) -> str:
    return _ALNUM.sub("", name.lower())


def release_names_match(label: str, title: str) -> bool:
    """True when a download name and a queue title are the same release."""
    left = _norm_name(label)
    right = _norm_name(title)
    if len(left) < 6 or len(right) < 6:
        return False
    return left in right or right in left


def _videos_in(root: Path, min_bytes: int) -> list[Path]:
    if not root.exists():
        return []
    found: list[Path] = []
    paths = root.rglob("*") if root.is_dir() else [root]
    for path in sorted(paths):
        if not path.is_file() or not is_video(path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < min_bytes:
            continue
        found.append(path)
    return found


def _names_western(target: Path) -> bool:
    if is_western_release_name(target.name):
        return True
    if not target.is_dir():
        return False
    try:
        children = list(target.iterdir())
    except OSError:
        return False
    return any(child.is_file() and is_western_release_name(child.name) for child in children)


def list_ready_western(
    download_root: Path,
    min_bytes: int,
    settle: int,
    now: float,
) -> list[Path]:
    """Finished Xunlei folders whose names look like Site.YY.MM.DD."""
    ready: list[Path] = []
    for target in iter_watch_targets(download_root):
        if not _names_western(target):
            continue
        if source_incomplete(target):
            continue
        mtime = source_mtime(target)
        if mtime and now - mtime < settle:
            continue
        if target.is_file():
            try:
                size = target.stat().st_size
            except OSError:
                continue
            if is_video(target) and size >= min_bytes:
                ready.append(target)
            continue
        if iter_videos(target, min_bytes):
            ready.append(target)
    return ready


def find_western_videos(
    download_root: Path,
    dest: Path,
    magnet_title: str,
    min_bytes: int,
) -> tuple[Path, list[Path]]:
    local = _videos_in(dest, min_bytes)
    if local:
        return dest, local
    needle = _norm_name(magnet_title)
    if len(needle) < 6 or not download_root.is_dir():
        raise ScrapeError("没有可归档的视频")
    matches: list[tuple[Path, list[Path]]] = []
    for child in sorted(download_root.iterdir()):
        if child.name.lower() in {"western", "云盘缓存文件", ".bt"}:
            continue
        label = _norm_name(child.stem if child.is_file() else child.name)
        if len(label) < 6:
            continue
        if label not in needle and needle not in label:
            continue
        if child.is_dir():
            videos = _videos_in(child, min_bytes)
            if videos:
                matches.append((child, videos))
        elif is_video(child) and child.stat().st_size >= min_bytes:
            matches.append((child, [child]))
    if not matches:
        raise ScrapeError("没有可归档的视频")
    matches.sort(key=lambda item: len(_norm_name(item[0].name)), reverse=True)
    return matches[0]


def _archive_stem(name: str) -> str:
    cleaned = release_text(name)
    if cleaned and is_western_release_name(cleaned):
        return _safe_stem(cleaned)
    return _safe_stem(name)


def _safe_stem(name: str) -> str:
    text = Path(name).name
    if text.lower().endswith(tuple(VIDEO_EXTS)):
        text = Path(text).stem
    cleaned = _UNSAFE.sub(".", text).strip(" .")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    return (cleaned or "video")[:160]


def _place_name(dest_dir: Path, stem: str, ext: str) -> str:
    taken = {path.name.lower() for path in dest_dir.iterdir()} if dest_dir.exists() else set()
    name = f"{stem}{ext}"
    if name.lower() not in taken:
        return name
    index = 2
    while f"{stem}-{index}{ext}".lower() in taken:
        index += 1
    return f"{stem}-{index}{ext}"


async def western_metadata(settings: Settings, info: dict) -> dict:
    meta = {
        "code": "",
        "title": (info.get("title") or "").strip(),
        "release_date": (info.get("date") or "")[:10],
        "runtime": "",
        "studio": (info.get("site") or "").strip(),
        "genres": [],
        "actors": info.get("performers") or [],
        "plot": "",
        "url": "",
        "cover": "",
        "uniqueid": (info.get("tpdb_id") or "").strip(),
        "uniqueid_type": "tpdb" if (info.get("tpdb_id") or "").strip() else "",
    }
    item_id = meta["uniqueid"]
    kind = info.get("tpdb_kind") or "scene"
    if item_id and (settings.tpdb_api_key or "").strip() and kind in ("scene", "movie"):
        try:
            detail = await fetch_detail(settings, kind, item_id)
        except TpdbError as exc:
            raise ScrapeError(f"刮削元数据失败: {exc}") from exc
        meta["title"] = detail.get("title") or meta["title"]
        meta["release_date"] = (detail.get("date") or meta["release_date"])[:10]
        meta["runtime"] = detail.get("duration") or ""
        meta["studio"] = detail.get("site") or meta["studio"]
        meta["genres"] = detail.get("tags") or []
        meta["actors"] = detail.get("performers") or meta["actors"]
        meta["plot"] = detail.get("description") or ""
        meta["url"] = detail.get("url") or ""
        meta["cover"] = detail.get("cover") or detail.get("background") or ""
    if not meta["title"]:
        raise ScrapeError("没有作品标题")
    return meta


def _match_names(src: Path, min_bytes: int) -> list[str]:
    names = [src.name]
    if not src.is_dir():
        return names
    for video in iter_videos(src, min_bytes):
        if video.name not in names:
            names.append(video.name)
    return names


async def scrape_western_source(settings: Settings, src: Path) -> dict:
    """Archive one finished download that never entered the jav-dl queue."""
    min_bytes = max(0, int(settings.scrape_min_mb) * 1024 * 1024)
    detail = None
    error = "没有匹配的欧美作品"
    for name in _match_names(src, min_bytes):
        try:
            detail = await fetch_by_filename(settings, name)
        except TpdbError as exc:
            error = str(exc)
            continue
        if detail:
            break
    if not detail:
        raise ScrapeError(error)
    info = {
        "kind": "western",
        "tpdb_id": detail.get("id") or "",
        "tpdb_kind": detail.get("kind") or "scene",
        "site": detail.get("site") or "",
        "title": detail.get("title") or src.stem,
        "date": detail.get("date") or "",
        "performers": detail.get("performers") or [],
    }
    return await scrape_western_job(settings, {"dest": str(src), "title": src.name}, info)


def _discard_finished_dir(src: Path, download_root: Path) -> None:
    if not src.is_dir():
        return
    if _videos_in(src, 0):
        return
    safe_rmtree(src, download_root)


def _discard_slug(dest: Path, download_root: Path) -> None:
    if not dest.is_dir() or dest.parent.name.lower() != "western":
        return
    if _videos_in(dest, 0):
        return
    safe_rmtree(dest, download_root)


def _commit_western(
    settings: Settings,
    job: dict,
    src: Path,
    videos: list[Path],
    meta: dict,
    poster: bytes | None,
) -> dict:
    root = settings.western_root
    if root is None:
        raise ScrapeError("未配置欧美归档目录")
    folder = studio_dir(root, meta.get("studio") or "")
    folder.mkdir(parents=True, exist_ok=True)
    nfo_xml = build_nfo(meta)
    written: list[Path] = []
    for video in videos:
        if video.suffix.lower() not in VIDEO_EXTS or is_incomplete(video):
            continue
        name = _place_name(folder, _archive_stem(video.name), video.suffix.lower() or ".mp4")
        target = folder / name
        shutil.move(str(video), str(target))
        target.chmod(0o644)
        (folder / f"{target.stem}.nfo").write_text(nfo_xml, encoding="utf-8")
        if poster:
            poster_path = folder / f"{target.stem}-poster.jpg"
            poster_path.write_bytes(poster)
            poster_path.chmod(0o644)
        written.append(target)
    if not written:
        raise ScrapeError("没有可归档的视频")
    _discard_finished_dir(src, settings.download_dir)
    _discard_slug(Path(job.get("dest") or ""), settings.download_dir)
    title = meta.get("title") or ""
    tpdb_id = (meta.get("uniqueid") or "").strip()
    return {
        "path": str(folder),
        "videos": [str(path) for path in written],
        "title": title,
        "entries": [{
            "path": f"{folder.name}/{path.name}",
            "tpdb_id": tpdb_id,
            "studio": folder.name,
            "title": title or path.stem,
            "has_nfo": 1,
            "has_poster": 1 if poster else 0,
        } for path in written],
    }


async def scrape_western_job(
    settings: Settings,
    job: dict,
    info: dict,
    found: tuple[Path, list[Path]] | None = None,
) -> dict:
    if settings.western_root is None:
        raise ScrapeError("未配置欧美归档目录")
    dest = Path(job.get("dest") or "")
    min_bytes = max(0, int(settings.scrape_min_mb) * 1024 * 1024)
    if found is None:
        src, videos = await asyncio.to_thread(
            find_western_videos,
            settings.download_dir,
            dest,
            job.get("title") or info.get("title") or "",
            min_bytes,
        )
    else:
        src, videos = found
    if await asyncio.to_thread(source_incomplete, src):
        raise ScrapeError("下载尚未完成")
    meta = await western_metadata(settings, info)
    poster = None
    cover = (meta.get("cover") or "").strip()
    if cover:
        try:
            poster = await fetch_cover_bytes(settings, cover, referer="https://theporndb.net/")
        except ScrapeError:
            poster = None
    result = await asyncio.to_thread(_commit_western, settings, job, src, videos, meta, poster)
    log.info("已归档欧美 %s -> %s", result.get("title") or job.get("title") or "", result["path"])
    return result

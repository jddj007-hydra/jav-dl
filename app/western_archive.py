from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.config import Settings
from app.nfo import build_nfo
from app.scrape import (
    ScrapeError,
    VIDEO_EXTS,
    fetch_cover_bytes,
    has_incomplete_files,
    is_incomplete,
    is_video,
)
from app.sources.tpdb import TpdbError, fetch_detail

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
            matches.append((child.parent, [child]))
    if not matches:
        raise ScrapeError("没有可归档的视频")
    matches.sort(key=lambda item: len(_norm_name(item[0].name)), reverse=True)
    return matches[0]


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = _UNSAFE.sub(".", stem).strip(" .")
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


async def scrape_western_job(settings: Settings, job: dict, info: dict) -> dict:
    root = settings.western_root
    if root is None:
        raise ScrapeError("未配置欧美归档目录")
    dest = Path(job.get("dest") or "")
    min_bytes = max(0, int(settings.scrape_min_mb) * 1024 * 1024)
    src, videos = find_western_videos(
        settings.download_dir,
        dest,
        job.get("title") or info.get("title") or "",
        min_bytes,
    )
    if any(is_incomplete(video) for video in videos):
        raise ScrapeError("下载尚未完成")
    if src.is_dir() and src.resolve() != settings.download_dir.resolve() and has_incomplete_files(src):
        raise ScrapeError("下载尚未完成")
    meta = await western_metadata(settings, info)
    folder = studio_dir(root, meta.get("studio") or "")
    folder.mkdir(parents=True, exist_ok=True)
    nfo_xml = build_nfo(meta)
    poster = None
    cover = (meta.get("cover") or "").strip()
    if cover:
        try:
            poster = await fetch_cover_bytes(settings, cover, referer="https://theporndb.net/")
        except ScrapeError:
            poster = None
    written: list[Path] = []
    for video in videos:
        if video.suffix.lower() not in VIDEO_EXTS or is_incomplete(video):
            continue
        name = _place_name(folder, _safe_stem(video.name), video.suffix.lower() or ".mp4")
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
    return {
        "path": str(folder),
        "videos": [str(path) for path in written],
        "title": meta.get("title") or "",
    }

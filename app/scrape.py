from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
from datetime import date
from pathlib import Path

from app.codes import extract_code, normalize_code, title_mentions_code
from app.western_magnets import is_western_release_name
from app.config import Settings
from app.httputil import site_client
from app.imgcache import trim_img_cache
from app.nfo import build_nfo
from app.sources.javbus import CACHE_VER, MetadataError, fetch_metadata

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".wmv", ".iso", ".ts", ".m2ts",
    ".rmvb", ".mov", ".mpg", ".mpeg", ".flv", ".webm",
}
SUB_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".smi", ".vtt"}
SKIP_DIR_NAMES = {
    "云盘缓存文件",
    "$recycle.bin",
    "recycle.bin",
    "system volume information",
    ".bt",
    "western",
}
PACK_RANGE_RE = re.compile(r"\d{3}\s*[-~]\s*\d{3}")

MONTH_RE = re.compile(r"(\d{4})[-/.](\d{1,2})")
BUCKET_DIRS = {"jav-dl", "xunlei", "western"}
log = logging.getLogger("app.scrape")


class ScrapeError(Exception):
    pass


def archive_month(release_date: str | None, today: date | None = None) -> str:
    m = MONTH_RE.match((release_date or "").strip())
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}"
    d = today or date.today()
    return d.strftime("%Y%m")


def is_incomplete(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if lower.endswith((".xltd", ".aria2", ".part", ".crdownload", ".tmp")) or name.endswith(".!qB") or lower.endswith(".!qb"):
        return True
    for extra in (".xltd", ".!qB", ".aria2", ".part"):
        if Path(str(path) + extra).exists():
            return True
    sibling = path.with_name(path.name + ".xltd")
    return sibling.exists()


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS and not is_incomplete(path)


def iter_videos(root: Path, min_bytes: int = 0) -> list[Path]:
    if not root.exists():
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.lower() in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not is_video(path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < min_bytes:
            continue
        found.append(path)
    return found


def has_incomplete_files(root: Path) -> bool:
    if not root.exists():
        return False
    for path in root.rglob("*"):
        if path.is_file() and is_incomplete(path):
            return True
    return False


def newest_mtime(root: Path) -> float:
    newest = 0.0
    if not root.exists():
        return 0.0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def looks_like_pack(name: str) -> bool:
    lower = name.lower()
    if "pack" in lower or "合集" in name:
        return True
    return bool(PACK_RANGE_RE.search(name))


def _candidate_dirs(download_root: Path) -> list[Path]:
    roots = [download_root]
    for sub in ("jav-dl", "xunlei"):
        path = download_root / sub
        if path.is_dir():
            roots.append(path)
    return roots


def find_code_videos(
    code: str,
    dest: Path,
    download_root: Path,
    min_bytes: int = 0,
) -> tuple[Path, list[Path]]:
    if dest.is_file():
        try:
            size = dest.stat().st_size
        except OSError:
            size = 0
        if is_video(dest) and size >= min_bytes:
            return dest, [dest]
    elif dest.exists():
        videos = iter_videos(dest, min_bytes)
        if videos:
            return dest, videos

    matches: list[tuple[Path, list[Path]]] = []
    seen: set[Path] = set()
    for root in _candidate_dirs(download_root):
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in sorted(children):
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if child.name in SKIP_DIR_NAMES or child.name.lower() in SKIP_DIR_NAMES:
                continue
            if child.is_file():
                if (
                    is_video(child)
                    and child.stat().st_size >= min_bytes
                    and title_mentions_code(child.name, code)
                    and not looks_like_pack(child.name)
                ):
                    matches.append((child, [child]))
                continue
            if not child.is_dir():
                continue
            folder_hit = normalize_code(child.name) == code or title_mentions_code(child.name, code)
            if not folder_hit or looks_like_pack(child.name):
                continue
            videos = iter_videos(child, min_bytes)
            if videos:
                matches.append((child, videos))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [m for m in matches if normalize_code(m[0].name) == code]
        return (exact[0] if exact else matches[0])
    raise ScrapeError("没有可归档的视频")


def iter_watch_targets(download_root: Path) -> list[Path]:
    if not download_root.is_dir():
        return []
    seen: set[Path] = set()
    out: list[Path] = []
    nested = {"jav-dl", "xunlei"}
    for root in _candidate_dirs(download_root):
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in sorted(children):
            if child.name in SKIP_DIR_NAMES or child.name.lower() in SKIP_DIR_NAMES:
                continue
            if child.name in nested and child.is_dir():
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(child)
    return out


def source_incomplete(path: Path) -> bool:
    if path.is_file():
        return is_incomplete(path)
    return has_incomplete_files(path)


def source_mtime(path: Path) -> float:
    if path.is_file():
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
    return newest_mtime(path)


def list_ready_sources(
    download_root: Path,
    min_bytes: int,
    settle: int,
    now: float,
) -> list[tuple[str, Path]]:
    ready: list[tuple[str, Path]] = []
    for target in iter_watch_targets(download_root):
        if source_incomplete(target):
            continue
        mtime = source_mtime(target)
        if mtime and now - mtime < settle:
            continue
        if looks_like_pack(target.name) or is_western_release_name(target.name):
            continue
        if target.is_file():
            if not is_video(target):
                continue
            try:
                size = target.stat().st_size
            except OSError:
                continue
            if size < min_bytes:
                continue
            code = extract_code(target.name)
            if code:
                ready.append((code, target))
            continue
        videos = iter_videos(target, min_bytes)
        if not videos:
            continue
        code = extract_code(target.name)
        if not code:
            for video in videos:
                if is_western_release_name(video.name):
                    continue
                code = extract_code(video.name)
                if code:
                    break
        if code:
            ready.append((code, target))
    return ready


def assign_video_names(code: str, sources: list[Path], taken: set[str] | None = None) -> list[tuple[Path, str]]:
    used = {n.lower() for n in (taken or set())}
    sources = list(sources)
    out: list[tuple[Path, str]] = []
    if not sources:
        return out

    def take(name: str) -> str:
        used.add(name.lower())
        return name

    if len(sources) == 1:
        ext = sources[0].suffix.lower() or ".mp4"
        name = f"{code}{ext}"
        if name.lower() in used:
            i = 2
            while f"{code}-{i}{ext}".lower() in used:
                i += 1
            name = f"{code}-{i}{ext}"
        out.append((sources[0], take(name)))
        return out

    idx = 1
    for src in sources:
        ext = src.suffix.lower() or ".mp4"
        while True:
            name = f"{code}-CD{idx}{ext}"
            idx += 1
            if name.lower() not in used:
                break
        out.append((src, take(name)))
    return out


def _sub_dest_name(sub: Path, old_stem: str, new_stem: str) -> str:
    if sub.name.startswith(old_stem):
        return new_stem + sub.name[len(old_stem):]
    return new_stem + sub.suffix


def safe_rmtree(path: Path, root: Path) -> None:
    if not path.exists():
        return
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return
    if resolved == base or not resolved.is_relative_to(base):
        return
    if resolved.parent == base and resolved.name.lower() in BUCKET_DIRS:
        return
    shutil.rmtree(resolved, ignore_errors=True)


async def resolve_metadata(settings: Settings, db, code: str) -> dict:
    key = f"{CACHE_VER}:{code}"
    cached = await db.get_metadata_any(key)
    if cached:
        return cached
    try:
        meta = await fetch_metadata(settings, code)
    except MetadataError as e:
        raise ScrapeError(f"刮削元数据失败: {e}") from e
    await db.put_metadata(key, meta)
    return meta


async def fetch_cover_bytes(settings: Settings, url: str, referer: str | None = None) -> bytes:
    if not url:
        raise ScrapeError("没有封面地址")
    cache_dir = settings.data_dir / "img_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    cached = cache_dir / key
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()
    async with site_client(settings) as client:
        page = referer or (settings.javbus_base.rstrip("/") + "/")
        r = await client.get(url, headers={"Referer": page})
    if r.status_code >= 400 or not r.content:
        raise ScrapeError("封面下载失败")
    if len(r.content) > 8 * 1024 * 1024:
        raise ScrapeError("封面过大")
    cached.write_bytes(r.content)
    trim_img_cache(cache_dir)
    return r.content


def write_images(dest_dir: Path, poster_bytes: bytes | None) -> tuple[bool, bool]:
    if not poster_bytes:
        poster = dest_dir / "poster.jpg"
        fanart = dest_dir / "fanart.jpg"
        return poster.is_file() and poster.stat().st_size > 0, fanart.is_file() and fanart.stat().st_size > 0
    poster = dest_dir / "poster.jpg"
    fanart = dest_dir / "fanart.jpg"
    if not poster.exists() or poster.stat().st_size == 0:
        poster.write_bytes(poster_bytes)
    if not fanart.exists() or fanart.stat().st_size == 0:
        fanart.write_bytes(poster_bytes)
    return True, True


def archive_videos(
    code: str,
    videos: list[Path],
    dest_dir: Path,
    nfo_xml: str,
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    taken = {p.name for p in dest_dir.iterdir() if p.is_file()}
    mapping = assign_video_names(code, videos, taken)
    written: list[Path] = []
    for src, name in mapping:
        dest = dest_dir / name
        old_parent = src.parent
        old_stem = src.stem
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        new_stem = dest.stem
        if old_parent.is_dir():
            for path in list(old_parent.iterdir()):
                if not path.is_file() or path.suffix.lower() not in SUB_EXTS:
                    continue
                if path.stem != old_stem and not path.name.startswith(old_stem + "."):
                    continue
                shutil.move(str(path), str(dest.with_name(_sub_dest_name(path, old_stem, new_stem))))
        (dest_dir / f"{dest.stem}.nfo").write_text(nfo_xml, encoding="utf-8")
        written.append(dest)
    return written


def _commit_jav(
    code: str,
    videos: list[Path],
    dest_dir: Path,
    nfo_xml: str,
    poster_bytes: bytes | None,
    src: Path,
    min_bytes: int,
    download_root: Path,
    cover: str,
) -> tuple[bool, bool]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_videos(code, videos, dest_dir, nfo_xml)
    has_poster, has_fanart = write_images(dest_dir, poster_bytes)
    if cover and not (has_poster and has_fanart):
        raise ScrapeError("封面写入失败")
    if not src.is_file() and not iter_videos(src, min_bytes):
        safe_rmtree(src, download_root)
    return has_poster, has_fanart


async def scrape_job(
    settings: Settings,
    db,
    job: dict,
    found: tuple[Path, list[Path]] | None = None,
) -> dict:
    code = (job.get("code") or "").strip().upper()
    if not code:
        raise ScrapeError("任务没有番号")
    dest = Path(job.get("dest") or "")
    min_bytes = max(0, int(settings.scrape_min_mb) * 1024 * 1024)
    if found is None:
        src, videos = await asyncio.to_thread(
            find_code_videos, code, dest, settings.download_dir, min_bytes,
        )
    else:
        src, videos = found
    if await asyncio.to_thread(source_incomplete, src):
        raise ScrapeError("下载尚未完成")

    meta = await resolve_metadata(settings, db, code)
    month = archive_month(meta.get("release_date"))
    dest_dir = settings.media_dir / month / code
    nfo_xml = build_nfo(meta)

    poster_bytes = None
    cover = (meta.get("cover") or "").strip()
    if cover:
        poster_bytes = await fetch_cover_bytes(settings, cover)

    has_poster, _has_fanart = await asyncio.to_thread(
        _commit_jav,
        code,
        videos,
        dest_dir,
        nfo_xml,
        poster_bytes,
        src,
        min_bytes,
        settings.download_dir,
        cover,
    )
    log.info("已归档 %s -> %s", code, f"{month}/{code}")

    return {
        "code": code,
        "month": month,
        "path": f"{month}/{code}",
        "archive_dir": str(dest_dir),
        "has_video": True,
        "has_nfo": True,
        "has_poster": has_poster,
    }

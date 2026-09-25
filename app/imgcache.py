from __future__ import annotations

from pathlib import Path

IMG_CACHE_LIMIT = 500 * 1024 * 1024


def trim_img_cache(cache_dir: Path, limit: int = IMG_CACHE_LIMIT) -> None:
    """Drop the oldest cached covers once the directory is over the limit."""
    try:
        paths = [path for path in cache_dir.iterdir() if path.is_file()]
    except OSError:
        return
    groups: dict[str, list[Path]] = {}
    for path in paths:
        key = path.name[:-3] if path.name.endswith(".ct") else path.name
        groups.setdefault(key, []).append(path)
    items: list[tuple[float, int, list[Path]]] = []
    total = 0
    for files in groups.values():
        size = 0
        newest = 0.0
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            size += stat.st_size
            newest = max(newest, stat.st_mtime)
        total += size
        items.append((newest, size, files))
    if total <= limit:
        return
    items.sort()
    for _mtime, size, files in items:
        if total <= limit:
            break
        for path in files:
            try:
                path.unlink()
            except OSError:
                continue
        total -= size

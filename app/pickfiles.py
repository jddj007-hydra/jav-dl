"""Decide which files in a multi-file torrent are worth downloading."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.textutil import format_size

VIDEO_EXT = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".m2ts",
    ".mpg", ".mpeg", ".rmvb", ".flv", ".m4v", ".iso",
}
# Non-video files smaller than this stay unchecked.
TINY_BYTES = 20 * 1000**2
_AD_RE = re.compile(
    r"(manko\.fun|sample|preview|trailer|样片|預告|预告|广告|廣告|宣传|宣傳)",
    re.IGNORECASE,
)


def _suffix(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def _basename(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).name or name


def default_selected(files: list[dict]) -> list[bool]:
    """Uncheck ads, samples, and tiny non-video files.

    If that would leave nothing, keep the largest file.
    """
    flags: list[bool] = []
    for item in files:
        name = str(item.get("path") or item.get("name") or "")
        base = _basename(name)
        ext = _suffix(base)
        size = int(item.get("size") or 0)
        skip = False
        if _AD_RE.search(name):
            skip = True
        elif ext not in VIDEO_EXT and size < TINY_BYTES:
            skip = True
        flags.append(not skip)
    if files and not any(flags):
        biggest = max(range(len(files)), key=lambda i: int(files[i].get("size") or 0))
        flags[biggest] = True
    return flags


def present_files(files: list[dict]) -> list[dict]:
    flags = default_selected(files)
    shown = []
    for item, selected in zip(files, flags):
        size = int(item.get("size") or 0)
        shown.append({
            "index": int(item["index"]),
            "name": _basename(str(item.get("name") or item.get("path") or "")),
            "size": size,
            "size_text": format_size(size),
            "selected": selected,
        })
    return shown


def aria_content_files(status: dict) -> list[dict] | None:
    """Real file entries, or None while aria2 still only has the magnet metadata."""
    raw = status.get("files") or []
    if not raw:
        return None
    parsed: list[dict] = []
    for item in raw:
        path = str(item.get("path") or "")
        name = _basename(path)
        if not name or "[METADATA]" in name.upper():
            return None
        try:
            index = int(item.get("index"))
            size = int(item.get("length") or 0)
        except (TypeError, ValueError):
            continue
        if index <= 0:
            continue
        parsed.append({"index": index, "name": name, "path": path, "size": size})
    if not parsed:
        return None
    if len(parsed) == 1 and parsed[0]["size"] <= 0:
        return None
    return parsed


def flatten_xunlei_files(resource: dict) -> list[dict]:
    children = ((resource.get("dir") or {}).get("resources") or [])
    if children:
        found: list[dict] = []
        for child in children:
            found.extend(flatten_xunlei_files(child))
        return found
    if resource.get("is_dir"):
        return []
    name = str(resource.get("name") or "")
    index = resource.get("file_index")
    if not name or index is None:
        return []
    try:
        size = int(resource.get("file_size") or 0)
        number = int(index)
    except (TypeError, ValueError):
        return []
    return [{"index": number, "name": name, "path": name, "size": size}]


def format_sub_file_index(selected: list[int], all_indexes: list[int]) -> str:
    """Xunlei sub_file_index. A one-file torrent stays `--1,`."""
    everything = sorted(set(int(i) for i in all_indexes))
    if len(everything) <= 1:
        return "--1,"
    chosen = sorted(set(int(i) for i in selected))
    if not chosen:
        raise ValueError("没有选择文件")
    if chosen == everything and everything == list(range(everything[0], everything[-1] + 1)):
        return f"{everything[0]}-{everything[-1]}"
    return ",".join(str(i) for i in chosen)


def format_select_file(selected: list[int]) -> str:
    """aria2 select-file is a 1-based comma list."""
    chosen = sorted(set(int(i) for i in selected))
    if not chosen:
        raise ValueError("没有选择文件")
    return ",".join(str(i) for i in chosen)

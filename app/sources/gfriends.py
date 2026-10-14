"""按名字从 gfriends 的 Filetree.json 取头像 URL。

只返回 CDN 地址，不下载图片。索引缓存在 settings.data_dir，失败时记日志并返回空串。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from app.config import Settings
from app.httputil import site_client

# 索引地址和头像前缀按顺序对应。先 jsDelivr，再 GitHub raw。
FILE_TREE_SOURCES: tuple[tuple[str, str], ...] = (
    (
        "https://cdn.jsdelivr.net/gh/gfriends/gfriends@master/Filetree.json",
        "https://cdn.jsdelivr.net/gh/gfriends/gfriends@master/",
    ),
    (
        "https://raw.githubusercontent.com/gfriends/gfriends/master/Filetree.json",
        "https://raw.githubusercontent.com/gfriends/gfriends/master/",
    ),
)
CACHE_NAME = "gfriends-filetree.json"
CACHE_TTL = 7 * 24 * 60 * 60
FAIL_TTL = 10 * 60

_WS = re.compile(r"\s+")
log = logging.getLogger("app.gfriends")

_names: dict[str, str] | None = None
_compact: dict[str, str] = {}
_cdn = ""
_fetched_at = 0.0
_dir = ""
_fail_at = 0.0
_fail_dir = ""
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def reset_cache() -> None:
    """清掉进程内索引。不删除 data 目录里的缓存文件。"""
    global _names, _compact, _cdn, _fetched_at, _dir, _fail_at, _fail_dir
    _names = None
    _compact = {}
    _cdn = ""
    _fetched_at = 0.0
    _dir = ""
    _fail_at = 0.0
    _fail_dir = ""


def _gate() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def _dir_key(data_dir: Path) -> str:
    return str(data_dir)


def _compact_name(value: str) -> str:
    return _WS.sub("", value)


def _build_compact(names: dict[str, str]) -> dict[str, str]:
    compact: dict[str, str] = {}
    exact: set[str] = set()
    for actor, rel in names.items():
        key = _compact_name(actor)
        if not key:
            continue
        if key not in compact or (actor == key and key not in exact):
            compact[key] = rel
        if actor == key:
            exact.add(key)
    return compact


def _flatten(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    content = data.get("Content")
    if not isinstance(content, dict):
        return {}
    flat: dict[str, str] = {}
    for company, actors in content.items():
        if not isinstance(company, str) or not company or not isinstance(actors, dict):
            continue
        for actor_name, filename in actors.items():
            if not isinstance(actor_name, str) or not isinstance(filename, str):
                continue
            if not actor_name or not filename:
                continue
            flat[actor_name] = f"Content/{company}/{filename}"
    return flat


def _cache_file(data_dir: Path) -> Path:
    return data_dir / CACHE_NAME


def _read_cache(data_dir: Path, now: float) -> tuple[dict[str, str], str, float] | None:
    path = _cache_file(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    names = raw.get("names")
    cdn = raw.get("cdn")
    fetched_at = raw.get("fetched_at")
    if not isinstance(names, dict) or not isinstance(cdn, str) or not cdn.endswith("/"):
        return None
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return None
    fetched = float(fetched_at)
    if now - fetched >= CACHE_TTL:
        return None
    flat = {
        key: value
        for key, value in names.items()
        if isinstance(key, str) and isinstance(value, str) and key and value
    }
    if not flat:
        return None
    return flat, cdn, fetched


def _write_cache(data_dir: Path, names: dict[str, str], cdn: str, fetched_at: float) -> None:
    path = _cache_file(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cdn": cdn, "fetched_at": fetched_at, "names": names}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("gfriends 索引缓存写入失败: %s", exc)


def _memory(now: float, data_dir: Path) -> tuple[dict[str, str], dict[str, str], str] | None:
    if _names is None or _dir != _dir_key(data_dir):
        return None
    if now - _fetched_at >= CACHE_TTL:
        return None
    return _names, _compact, _cdn


def _cooling(now: float, data_dir: Path) -> bool:
    if _fail_at <= 0 or _fail_dir != _dir_key(data_dir):
        return False
    return now - _fail_at < FAIL_TTL


def _remember(data_dir: Path, names: dict[str, str], cdn: str, fetched_at: float) -> None:
    global _names, _compact, _cdn, _fetched_at, _dir, _fail_at, _fail_dir
    _names = names
    _compact = _build_compact(names)
    _cdn = cdn
    _fetched_at = fetched_at
    _dir = _dir_key(data_dir)
    _fail_at = 0.0
    _fail_dir = ""


def _note_failure(data_dir: Path, now: float) -> None:
    global _fail_at, _fail_dir
    _fail_at = now
    _fail_dir = _dir_key(data_dir)


def _lookup(names: dict[str, str], compact: dict[str, str], cdn: str, name: str) -> str:
    rel = names.get(name)
    if rel:
        return cdn + rel
    key = _compact_name(name)
    if not key:
        return ""
    rel = compact.get(key)
    if not rel:
        return ""
    return cdn + rel


async def _download(settings: Settings) -> tuple[dict[str, str], str]:
    last = "没有可用的索引源"
    async with site_client(settings) as client:
        for url, cdn in FILE_TREE_SOURCES:
            try:
                response = await client.get(url)
                response.raise_for_status()
                names = _flatten(response.json())
                if not names:
                    raise ValueError("Filetree.json 没有演员条目")
                return names, cdn
            except Exception as exc:
                last = f"{url} ({exc})"
                log.warning("gfriends 索引失败 %s: %s", url, exc)
    raise RuntimeError(last)


async def _load(settings: Settings) -> tuple[dict[str, str], dict[str, str], str] | None:
    data_dir = Path(settings.data_dir)
    now = time.time()
    fresh = _memory(now, data_dir)
    if fresh is not None:
        return fresh
    if _cooling(now, data_dir):
        return None
    async with _gate():
        now = time.time()
        fresh = _memory(now, data_dir)
        if fresh is not None:
            return fresh
        if _cooling(now, data_dir):
            return None
        cached = _read_cache(data_dir, now)
        if cached is not None:
            names, cdn, fetched_at = cached
            _remember(data_dir, names, cdn, fetched_at)
            return _memory(now, data_dir)
        try:
            names, cdn = await _download(settings)
        except Exception as exc:
            _note_failure(data_dir, time.time())
            log.warning("gfriends 索引下载失败，10 分钟内不再重试: %s", exc)
            return None
        fetched_at = time.time()
        _remember(data_dir, names, cdn, fetched_at)
        _write_cache(data_dir, names, cdn, fetched_at)
        return _memory(fetched_at, data_dir)


async def actor_photo(settings: Settings, name: str) -> str:
    """按名字返回头像 URL。精确匹配，再去掉空白后精确匹配。失败返回空串。"""
    try:
        if not str(name or "").strip():
            return ""
        loaded = await _load(settings)
        if not loaded:
            return ""
        names, compact, cdn = loaded
        return _lookup(names, compact, cdn, str(name))
    except Exception as exc:
        log.warning("gfriends 头像查询失败: %s", exc)
        return ""

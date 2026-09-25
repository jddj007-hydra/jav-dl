from __future__ import annotations

import base64
import logging
import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.codes import title_mentions_code
from app.config import Settings
from app.httputil import site_client
from app.ranking import detect_tags, is_pack
from app.textutil import parse_size, strip_em
from app.trackers import magnet_for

# 同一个番号或同一组关键词，这段时间内直接用上次的磁链，不再打磁力猫。
MAGNET_CACHE_TTL = 10 * 60
MAGNET_CACHE_MAX = 200
_magnet_cache: dict[tuple, tuple[float, list[dict]]] = {}
log = logging.getLogger("app.clm")

ATOB_RE = re.compile(r"window\.atob\(\"([^\"]+)\"\)")
LOC_RE = re.compile(r"location\.(?:href|hree)\s*=\s*['\"]([^'\"]+)['\"]", re.I)
INFO_RE = re.compile(r"/information/([a-zA-Z0-9]{32,40})")


class MagnetSearchError(Exception):
    pass


def _b64_word(code: str) -> str:
    return base64.b64encode(code.encode("utf-8")).decode("ascii")


def decode_atob_blobs(html: str) -> list[str]:
    from urllib.parse import unquote

    blobs: list[str] = []
    for payload in ATOB_RE.findall(html):
        try:
            blobs.append(unquote(base64.b64decode(payload).decode("utf-8")))
        except Exception:
            continue
    return blobs


def unwrap_search_html(html: str) -> tuple[str, str | None]:
    """Return (html_to_parse, js_redirect_url)."""
    blobs = decode_atob_blobs(html)
    for blob in blobs:
        m = LOC_RE.search(blob)
        if m:
            return html, m.group(1)
        if "SearchListTitle_result_title" in blob or "Search_list_wrapper" in blob:
            return blob, None
    m = LOC_RE.search(html)
    if m:
        return html, m.group(1)
    return html, None


def clm_id_to_infohash(cid: str) -> str | None:
    cid = cid.strip()
    if re.fullmatch(r"[a-fA-F0-9]{40}", cid):
        return cid.lower()
    try:
        pad = cid.upper() + "=" * ((8 - len(cid) % 8) % 8)
        digest = base64.b32decode(pad)
    except Exception:
        return None
    if len(digest) != 20:
        return None
    return digest.hex()


def parse_search_html(html: str, code: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    seen: set[str] = set()
    for li in soup.select("#Search_list_wrapper > li"):
        a = li.select_one("a.SearchListTitle_result_title")
        if not a or not a.get("href"):
            continue
        m = INFO_RE.search(a["href"])
        if not m:
            continue
        info_hash = clm_id_to_infohash(m.group(1))
        if not info_hash or info_hash in seen:
            continue
        seen.add(info_hash)
        title = strip_em(a.decode_contents() if a.decode_contents() else a.get_text())
        info = li.select_one(".Search_list_info")
        info_text = info.get_text(" ", strip=True) if info else ""
        heat_el = li.select_one(".Search_result_type")
        heat = 0
        if heat_el:
            hm = re.search(r"(\d+)", heat_el.get_text())
            heat = int(hm.group(1)) if hm else 0
        size_m = re.search(r"文件大小：\s*([0-9.]+\s*[A-Za-z]+)", info_text)
        date_m = re.search(r"创建时间：\s*([\d-]+)", info_text)
        ext_m = re.search(r"文件格式：\s*(\S+)", info_text)
        size_text = size_m.group(1) if size_m else ""
        size_bytes = parse_size(size_text)
        tags = detect_tags(title)
        pack = is_pack(title, size_bytes)
        items.append(
            {
                "info_hash": info_hash,
                "title": title,
                "size": size_text,
                "size_bytes": size_bytes,
                "heat": heat,
                "date": date_m.group(1) if date_m else "",
                "ext": ext_m.group(1) if ext_m else "",
                "tags": tags,
                "source": "clm",
                "magnet": magnet_for(info_hash, title),
                "pack": pack,
                "relevant": title_mentions_code(title, code),
            }
        )
    return items


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    r = await client.get(url)
    r.raise_for_status()
    return r


async def fetch_search_page(client: httpx.AsyncClient, url: str, hops: int = 0) -> str:
    if hops > 5:
        raise MagnetSearchError("磁力猫跳转过多")
    r = await _get(client, url)
    html, redirect = unwrap_search_html(r.text)
    if redirect:
        nxt = urljoin(str(r.url), redirect)
        return await fetch_search_page(client, nxt, hops + 1)
    return html


def clear_magnet_cache() -> None:
    _magnet_cache.clear()


def _origin(raw: str | None) -> str:
    return (raw or "").strip().rstrip("/")


def _search_origins(settings: Settings) -> list[str]:
    """Primary search domain, then the backup domain when it is different."""
    primary = _origin(settings.clm_search)
    backup = _origin(getattr(settings, "clm_search_backup", ""))
    origins: list[str] = []
    seen: set[str] = set()
    for base in (primary, backup):
        key = base.lower()
        if not base or key in seen:
            continue
        seen.add(key)
        origins.append(base)
    return origins


def _copy_items(items: list[dict]) -> list[dict]:
    copied: list[dict] = []
    for item in items:
        row = dict(item)
        tags = row.get("tags")
        if isinstance(tags, list):
            row["tags"] = list(tags)
        copied.append(row)
    return copied


def _cache_key(query: str, pages: int, settings: Settings) -> tuple:
    origins = tuple(base.lower() for base in _search_origins(settings))
    home = _origin(settings.clm_home).lower()
    return (query.strip().casefold(), pages, origins, home)


def _cache_get(key: tuple, now: float) -> list[dict] | None:
    row = _magnet_cache.get(key)
    if row is None:
        return None
    stored, items = row
    if now - stored >= MAGNET_CACHE_TTL:
        _magnet_cache.pop(key, None)
        return None
    return _copy_items(items)


def _cache_put(key: tuple, items: list[dict], now: float) -> None:
    _magnet_cache[key] = (now, _copy_items(items))
    expired = [
        cached_key
        for cached_key, (stored, _cached_items) in _magnet_cache.items()
        if now - stored >= MAGNET_CACHE_TTL
    ]
    for cached_key in expired:
        _magnet_cache.pop(cached_key, None)
    while len(_magnet_cache) > MAGNET_CACHE_MAX:
        oldest = min(_magnet_cache, key=lambda cached_key: _magnet_cache[cached_key][0])
        _magnet_cache.pop(oldest, None)


async def _collect_pages(
    client: httpx.AsyncClient,
    base: str,
    word: str,
    code: str,
    pages: int,
) -> tuple[list[dict], Exception | None]:
    collected: list[dict] = []
    seen: set[str] = set()
    last_err: Exception | None = None
    for page in range(1, pages + 1):
        query_url = f"{base}/search?word={word}&sort=hits"
        if page > 1:
            query_url += f"&p={page}"
        try:
            html = await fetch_search_page(client, query_url)
        except MagnetSearchError as exc:
            last_err = exc
            break
        except httpx.HTTPError as exc:
            last_err = exc
            break
        chunk = parse_search_html(html, code)
        if not chunk:
            break
        for item in chunk:
            if item["info_hash"] in seen:
                continue
            seen.add(item["info_hash"])
            collected.append(item)
    return collected, last_err


async def search_magnets(settings: Settings, code: str, pages: int = 2) -> list[dict]:
    query = (code or "").strip()
    if not query:
        return []
    pages = max(1, int(pages))
    key = _cache_key(query, pages, settings)
    cached = _cache_get(key, time.monotonic())
    if cached is not None:
        return cached

    word = _b64_word(query)
    origins = _search_origins(settings)
    home = _origin(settings.clm_home)
    if not origins and home:
        origins = [home]
    async with site_client(settings) as client:
        if home:
            try:
                await client.get(home + "/")
            except httpx.HTTPError:
                pass
        collected: list[dict] = []
        last_err: Exception | None = None
        for index, base in enumerate(origins):
            items, err = await _collect_pages(client, base, word, query, pages)
            if items:
                collected = items
                last_err = None
                break
            last_err = err or last_err
            if index == 0 and len(origins) > 1:
                log.info("磁力猫主域没有结果，改用备用搜索域")
        tried = {base.lower() for base in origins}
        if not collected and home and home.lower() not in tried:
            items, err = await _collect_pages(client, home, word, query, pages)
            if items:
                collected = items
                last_err = None
            else:
                last_err = err or last_err
        if not collected and last_err:
            if isinstance(last_err, MagnetSearchError):
                raise last_err
            raise MagnetSearchError(f"磁力猫请求失败: {last_err}")
        _cache_put(key, collected, time.monotonic())
        return _copy_items(collected)

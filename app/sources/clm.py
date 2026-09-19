from __future__ import annotations

import base64
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.codes import title_mentions_code
from app.config import Settings
from app.httputil import site_client
from app.ranking import detect_tags, is_pack
from app.textutil import parse_size, strip_em
from app.trackers import magnet_for

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


async def search_magnets(settings: Settings, code: str, pages: int = 2) -> list[dict]:
    word = _b64_word(code)
    base = settings.clm_search.rstrip("/")
    async with site_client(settings) as client:
        try:
            await client.get(settings.clm_home.rstrip("/") + "/")
        except httpx.HTTPError:
            pass
        collected: list[dict] = []
        seen: set[str] = set()
        last_err: Exception | None = None
        for page in range(1, pages + 1):
            q = f"{base}/search?word={word}&sort=hits"
            if page > 1:
                q += f"&p={page}"
            try:
                html = await fetch_search_page(client, q)
            except httpx.HTTPError as e:
                last_err = e
                break
            chunk = parse_search_html(html, code)
            if not chunk and page == 1:
                # maybe the configured domain bounced poorly; try home origin
                home = settings.clm_home.rstrip("/")
                if home != base:
                    try:
                        html = await fetch_search_page(
                            client, f"{home}/search?word={word}&sort=hits"
                        )
                        chunk = parse_search_html(html, code)
                    except httpx.HTTPError as e:
                        last_err = e
            if not chunk:
                break
            for it in chunk:
                if it["info_hash"] in seen:
                    continue
                seen.add(it["info_hash"])
                collected.append(it)
        if not collected and last_err:
            raise MagnetSearchError(f"磁力猫请求失败: {last_err}")
        return collected

"""JavDB 元数据。只在 JavBus 404 时由 resolve_metadata 调用。"""
from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.codes import normalize_code
from app.config import Settings, site_origin
from app.httputil import site_client
from app.sources.javbus import MetadataError, detect_blocker

_GENDER = re.compile(r"[♀♂]")
_SKIP_NAMES = frozenset({"複製", "复制", "复制番号", "複製番号"})
_FIELD_MAP = {
    "日期": "release_date",
    "發行日期": "release_date",
    "发行日期": "release_date",
    "片長": "runtime",
    "時長": "runtime",
    "时长": "runtime",
    "片长": "runtime",
    "長度": "runtime",
    "长度": "runtime",
    "導演": "director",
    "导演": "director",
    "製作商": "studio",
    "制作商": "studio",
    "片商": "studio",
    "發行商": "label",
    "发行商": "label",
    "發行": "label",
    "发行": "label",
    "系列": "series",
}
_GENRE_KEYS = frozenset({"類別", "类别", "標籤", "标签"})
_ACTOR_KEYS = frozenset({"演員", "演员", "演員列表"})
_BLOCKED = "JavDB 被拦截，返回了验证页。换代理或稍后再试"
_MISSING = "JavDB 也没有该番号"
_COVER_SELECTOR = (
    ".movie-panel .image img, .video-cover img, img.video-cover, .column-video-cover img"
)


def _abs(base: str, url: str | None) -> str:
    if not url:
        return ""
    text = str(url).strip()
    if not text or text.startswith(("javascript:", "data:")):
        return ""
    return urljoin(base.rstrip("/") + "/", text)


def _clean_name(text: str) -> str:
    return " ".join(_GENDER.sub("", text or "").split()).strip()


def _img_url(img) -> str:
    if img is None:
        return ""
    return (img.get("data-src") or img.get("data-original") or img.get("src") or "").strip()


def _on_base(base: str, url: str) -> bool:
    base_host = (urlparse(base).hostname or "").lower().rstrip(".")
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not base_host or not host:
        return False
    return host == base_host or host.endswith("." + base_host)


def _box_code(box) -> str:
    ordered: list[str] = []
    uid = box.select_one(".uid")
    if uid is not None:
        ordered.append(uid.get_text(strip=True))
    strong = box.select_one(".video-title strong")
    if strong is not None:
        ordered.append(strong.get_text(strip=True))
    title = (box.get("title") or "").strip()
    if title:
        ordered.append(title.split()[0])
    video = box.select_one(".video-title")
    if video is not None:
        text = video.get_text(" ", strip=True)
        if text:
            ordered.append(text.split()[0])
    if strong is None:
        any_strong = box.select_one("strong")
        if any_strong is not None:
            ordered.append(any_strong.get_text(strip=True))
    for raw in ordered:
        found = normalize_code(raw)
        if found:
            return found
    return ""


def parse_javdb_search(html: str, base: str, code: str) -> str:
    """返回番号一致的详情绝对地址。对不上或没有结果时返回空串。"""
    wanted = normalize_code(code) or ""
    if not wanted or not html:
        return ""
    root = (site_origin(base) or base).rstrip("/")
    soup = BeautifulSoup(html, "lxml")
    for box in soup.select("a.box"):
        if _box_code(box) != wanted:
            continue
        href = _abs(root, box.get("href"))
        if href and _on_base(root, href):
            return href
    return ""


def _panel_parts(block, base: str) -> tuple[str, str, list[dict]]:
    strong = block.find("strong")
    value_el = block.select_one(".value")
    if strong is not None:
        key = strong.get_text(strip=True).rstrip(":：").strip()
    else:
        raw = block.get_text(" ", strip=True)
        key = re.split(r"[:：]", raw, maxsplit=1)[0].strip()
    scope = value_el if value_el is not None else block
    links: list[dict] = []
    for anchor in scope.select("a"):
        name = _clean_name(anchor.get_text(" ", strip=True))
        if not name or name in _SKIP_NAMES:
            continue
        links.append({"name": name, "url": _abs(base, anchor.get("href"))})
    if value_el is not None:
        text = _clean_name(value_el.get_text(" ", strip=True))
    else:
        raw = block.get_text(" ", strip=True)
        if strong is not None:
            prefix = strong.get_text(strip=True)
            text = raw[len(prefix):].lstrip(":： ").strip() if raw.startswith(prefix) else raw
        else:
            parts = re.split(r"[:：]", raw, maxsplit=1)
            text = parts[1].strip() if len(parts) > 1 else ""
        text = _clean_name(text)
    return key, text, links


def _named(text: str, links: list[dict]) -> tuple[str, str]:
    if links:
        return links[0]["name"], links[0].get("url") or ""
    return text, ""


def _people(anchors, base: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for anchor in anchors:
        name = _clean_name(anchor.get_text(" ", strip=True))
        if not name or name in _SKIP_NAMES or name in seen:
            continue
        seen.add(name)
        img = anchor.select_one("img")
        photo = _abs(base, _img_url(img)) if img is not None else ""
        if not photo:
            photo = _abs(base, anchor.get("data-original") or anchor.get("data-src") or "")
        found.append({
            "name": name,
            "photo": photo,
            "url": _abs(base, anchor.get("href")),
        })
    return found


def _genres_from_anchors(anchors, base: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for anchor in anchors:
        name = _clean_name(anchor.get_text(strip=True))
        if not name or name in _SKIP_NAMES or name in seen:
            continue
        seen.add(name)
        found.append({"name": name, "url": _abs(base, anchor.get("href"))})
    return found


def _title_from(soup: BeautifulSoup, code: str) -> str:
    heading = soup.select_one("h2.title")
    if heading is None:
        return ""
    title = heading.get_text(" ", strip=True)
    strong = heading.select_one("strong")
    if strong is not None:
        label = strong.get_text(strip=True)
        if label and title.startswith(label):
            title = title[len(label):].strip(" -–—")
    code_up = (code or "").upper()
    if code_up and title.upper().startswith(code_up):
        title = title[len(code):].strip(" -–—")
    for suffix in (" - JavDB", " | JavDB"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title.strip()


def parse_javdb_detail(html: str, base: str, code: str, page_url: str = "") -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    root = (site_origin(base) or base).rstrip("/")
    title = _title_from(soup, code)
    cover_img = soup.select_one(_COVER_SELECTOR)
    cover = _abs(root, _img_url(cover_img)) if cover_img is not None else ""

    release_date = ""
    runtime = ""
    director = ""
    studio, studio_url = "", ""
    label, label_url = "", ""
    series, series_url = "", ""
    parsed: list[tuple[str, str, list[dict]]] = []
    for block in soup.select(".movie-panel .panel-block"):
        key, text, links = _panel_parts(block, root)
        parsed.append((key, text, links))
        field = _FIELD_MAP.get(key)
        if field == "release_date":
            release_date = text
        elif field == "runtime":
            runtime = text
        elif field == "director":
            director = links[0]["name"] if links else text
        elif field == "studio":
            studio, studio_url = _named(text, links)
        elif field == "label":
            label, label_url = _named(text, links)
        elif field == "series":
            series, series_url = _named(text, links)

    actors = _people(
        soup.select(".movie-panel .actors a, .panel-block .actors a"),
        root,
    )
    if not actors:
        for key, _text, links in parsed:
            if key not in _ACTOR_KEYS:
                continue
            for link in links:
                if link["name"] in {item["name"] for item in actors}:
                    continue
                actors.append({"name": link["name"], "photo": "", "url": link.get("url") or ""})

    genres = _genres_from_anchors(
        soup.select(".movie-panel .tags a, .panel-block .tags a"),
        root,
    )
    if not genres:
        for key, _text, links in parsed:
            if key not in _GENRE_KEYS:
                continue
            for link in links:
                if link["name"] in {item["name"] for item in genres}:
                    continue
                genres.append({"name": link["name"], "url": link.get("url") or ""})

    samples: list[dict] = []
    for anchor in soup.select(".preview-images a"):
        img = anchor.find("img")
        thumb = _abs(root, _img_url(img)) if img is not None else ""
        full = _abs(root, anchor.get("href")) or thumb
        if not (thumb or full):
            continue
        samples.append({"thumb": thumb or full, "full": full or thumb})

    if not (title or cover or studio or label or series or director or release_date or actors):
        raise MetadataError(_MISSING, 404)

    url = page_url or ""
    if not url:
        canonical = soup.select_one("link[rel=canonical]")
        if canonical is not None and canonical.get("href"):
            url = _abs(root, canonical.get("href"))

    return {
        "code": code,
        "title": title,
        "cover": cover or None,
        "actors": actors,
        "studio": studio,
        "studio_url": studio_url,
        "label": label,
        "label_url": label_url,
        "series": series,
        "series_url": series_url,
        "director": director,
        "release_date": release_date,
        "runtime": runtime,
        "genres": genres,
        "samples": samples,
        "source": "javdb",
        "url": url,
    }


def _request_base(settings: Settings) -> str:
    raw = (getattr(settings, "javdb_base", "") or "").strip()
    if not raw:
        return ""
    return site_origin(raw) or raw.rstrip("/")


def _headers(settings: Settings, base: str) -> dict[str, str]:
    headers = {"Referer": base.rstrip("/") + "/"}
    cookie = (getattr(settings, "javdb_cookie", "") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _final_url(response: httpx.Response) -> str:
    try:
        return str(response.url)
    except RuntimeError:
        return ""


def _raise_for_response(response: httpx.Response) -> None:
    if detect_blocker(_final_url(response), response.text):
        raise MetadataError(_BLOCKED, 503)
    if response.status_code == 404:
        raise MetadataError(_MISSING, 404)
    if response.status_code >= 400:
        raise MetadataError(f"JavDB HTTP {response.status_code}", response.status_code)


async def fetch_javdb_metadata(settings: Settings, code: str) -> dict:
    base = _request_base(settings)
    if not base:
        raise MetadataError("JavDB 未启用")
    headers = _headers(settings, base)
    search_url = f"{base}/search?q={quote(code)}&f=all"
    async with site_client(settings) as client:
        try:
            response = await client.get(search_url, headers=headers)
        except httpx.HTTPError as exc:
            raise MetadataError(f"JavDB 请求失败: {exc}") from exc
        _raise_for_response(response)
        detail_url = parse_javdb_search(response.text, base, code)
        if not detail_url:
            raise MetadataError(_MISSING, 404)
        try:
            detail = await client.get(detail_url, headers=headers)
        except httpx.HTTPError as exc:
            raise MetadataError(f"JavDB 请求失败: {exc}") from exc
    _raise_for_response(detail)
    return parse_javdb_detail(detail.text, base, code, page_url=str(detail_url))

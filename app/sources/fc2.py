from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.codes import fc2_number, normalize_code
from app.config import Settings
from app.httputil import site_client
from app.sources.javbus import MetadataError

FC2_BASE = "https://adult.contents.fc2.com"
_THUMB_HOST_RE = re.compile(
    r"^https?://contents-thumbnail\d*\.fc2\.com/w\d+/(.+)$",
    re.I,
)
_SALE_DATE_RE = re.compile(r"販売日\s*[:：]\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})")
_MISSING_TITLE = re.compile(
    r"未找到您要找的商品|お探しの商品が見つかりません|商品が見つかりません|Page Not Found",
    re.I,
)
_NOIMAGE_RE = re.compile(r"noimage|contents/images/.*/no[_-]?image", re.I)


def _abs(url: str | None) -> str:
    if not url:
        return ""
    return urljoin(FC2_BASE + "/", url)


def _full_image(url: str | None) -> str:
    """Upgrade FC2 thumbnail CDN URLs to the underlying storage object."""
    absolute = _abs(url)
    if not absolute:
        return ""
    if absolute.startswith("//"):
        absolute = "https:" + absolute
    matched = _THUMB_HOST_RE.fullmatch(absolute)
    if matched:
        return "https://" + matched.group(1)
    parsed = urlparse(absolute)
    if parsed.scheme == "http":
        absolute = "https://" + absolute[len("http://") :]
    return absolute


def _is_placeholder(url: str) -> bool:
    return bool(url) and bool(_NOIMAGE_RE.search(url))


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.select_one(f'meta[property="{name}"], meta[name="{name}"]')
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _strip_code_prefix(text: str, code: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    upper = value.upper()
    code_upper = (code or "").upper()
    if code_upper and upper.startswith(code_upper):
        return value[len(code) :].strip(" -–—|")
    compact = code_upper.replace("-", " ")
    if compact and upper.startswith(compact):
        return value[len(compact) :].strip(" -–—|")
    return value


def _title_from_header(soup: BeautifulSoup) -> str:
    h3 = soup.select_one(".items_article_headerInfo h3")
    if not h3:
        return ""
    node = BeautifulSoup(str(h3), "lxml").select_one("h3")
    if node is None:
        return h3.get_text(" ", strip=True)
    for bad in node.select(".items_article_saleTag, span[style*='zoom:0.01']"):
        bad.decompose()
    return node.get_text(" ", strip=True)


def _sale_date(soup: BeautifulSoup) -> str:
    for node in soup.select(".items_article_headerInfo p, .items_article_softDevice, .items_article_headerInfo"):
        text = node.get_text(" ", strip=True)
        matched = _SALE_DATE_RE.search(text)
        if matched:
            year, month, day = matched.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return ""


def _runtime(soup: BeautifulSoup) -> str:
    info = soup.select_one(".items_article_info")
    text = (info.get_text(strip=True) if info else "") or ""
    matched = re.fullmatch(r"(\d+):(\d{2})", text)
    if matched:
        return f"{int(matched.group(1))}分鐘"
    return text


def _seller(soup: BeautifulSoup) -> tuple[str, str]:
    link = soup.select_one(".items_article_writer a")
    if link:
        name = link.get_text(strip=True)
        return name, _abs(link.get("href"))
    writer = soup.select_one(".items_article_writer")
    if not writer:
        return "", ""
    text = writer.get_text(" ", strip=True)
    name = re.sub(r"^by\s+", "", text, flags=re.I).strip()
    return name, ""


def _genres(soup: BeautifulSoup) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for a in soup.select(".items_article_TagArea a.tag, .items_article_TagArea a"):
        name = a.get_text(strip=True)
        if not name or name in seen or name == "商品タグ":
            continue
        seen.add(name)
        found.append({"name": name, "url": ""})
    return found


def _samples(soup: BeautifulSoup) -> list[dict]:
    samples: list[dict] = []
    seen: set[str] = set()
    for a in soup.select(".items_article_SampleImagesArea a, .items_article_SampleImages a"):
        img = a.find("img")
        thumb = _full_image(img.get("src") if img else "")
        full = _full_image(a.get("href") or thumb)
        if not (thumb or full):
            continue
        if _is_placeholder(thumb) or _is_placeholder(full):
            continue
        key = full or thumb
        if key in seen:
            continue
        seen.add(key)
        samples.append({"thumb": thumb or full, "full": full or thumb})
    return samples


def parse_fc2(html: str, code: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    page_title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    if _MISSING_TITLE.search(page_title):
        raise MetadataError("未找到该番号", 404)
    if not soup.select_one(".items_article_headerInfo") and not _meta_content(soup, "og:title"):
        raise MetadataError("未找到该番号", 404)

    normalized = normalize_code(code) or code
    og_title = _meta_content(soup, "og:title")
    title = _strip_code_prefix(og_title, normalized) or _title_from_header(soup)
    title = _strip_code_prefix(title, normalized)
    if not title:
        raise MetadataError("未找到该番号", 404)
    if _MISSING_TITLE.search(title):
        raise MetadataError("未找到该番号", 404)

    cover = _full_image(_meta_content(soup, "og:image"))
    if not cover or _is_placeholder(cover):
        thumb = soup.select_one(".items_article_MainitemThumb img")
        cover = _full_image(thumb.get("src") if thumb else "")
    if _is_placeholder(cover):
        cover = ""

    studio, _seller_url = _seller(soup)
    plot = _strip_code_prefix(_meta_content(soup, "og:description", "description"), normalized)
    number = fc2_number(normalized) or ""
    url = f"{FC2_BASE}/article/{number}/" if number else FC2_BASE

    return {
        "code": normalized,
        "title": title,
        "cover": cover,
        "actors": [],
        "studio": studio,
        # FC2 卖家主页不是 JavBus 目录，不写 url，详情里只显示名字。
        "studio_url": "",
        "label": "",
        "label_url": "",
        "series": "",
        "series_url": "",
        "director": "",
        "release_date": _sale_date(soup),
        "runtime": _runtime(soup),
        "genres": _genres(soup),
        "samples": _samples(soup),
        "plot": plot,
        "source": "fc2",
        "url": url,
    }


async def fetch_fc2_metadata(settings: Settings, code: str) -> dict:
    number = fc2_number(code)
    if not number:
        raise MetadataError("不是 FC2 番号")
    normalized = normalize_code(code) or f"FC2-PPV-{number}"
    url = f"{FC2_BASE}/article/{number}/"
    headers = {"Referer": FC2_BASE + "/", "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"}
    async with site_client(settings) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise MetadataError(f"FC2 请求失败: {exc}") from exc
    if response.status_code == 404:
        raise MetadataError("未找到该番号", 404)
    if response.status_code >= 400:
        raise MetadataError(f"FC2 HTTP {response.status_code}", response.status_code)
    return parse_fc2(response.text, normalized)

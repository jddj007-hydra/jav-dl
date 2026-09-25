from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.codes import normalize_code
from app.config import Settings
from app.httputil import site_client

DATE_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}$")

AGE_COOKIE = "age=verified; existmag=all"
CACHE_VER = "v2"


class MetadataError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _abs(base: str, url: str | None) -> str:
    if not url:
        return ""
    return urljoin(base.rstrip("/") + "/", url)


def _info_fields(soup: BeautifulSoup, base: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in soup.select("div.col-md-3.info p, div.info p"):
        header = p.select_one("span.header")
        if not header:
            continue
        key = header.get_text(strip=True).rstrip(":：").strip()
        links = []
        for a in p.select("a"):
            name = a.get_text(strip=True)
            if not name or name == "多選提交":
                continue
            links.append({"name": name, "url": _abs(base, a.get("href"))})
        text = p.get_text(" ", strip=True)
        prefix = header.get_text(strip=True)
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip(":： ").strip()
        out[key] = {"text": text, "links": links}
    return out


def _first_link_or_text(field: dict | None) -> str:
    if not field:
        return ""
    if field.get("links"):
        return field["links"][0]["name"]
    return (field.get("text") or "").strip()


def parse_javbus(html: str, base: str, code: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one("#ageVerify"):
        raise MetadataError("JavBus 需要年龄确认，cookie 未生效")

    h3 = soup.select_one("h3")
    page_title = (h3.get_text(strip=True) if h3 else "") or (
        soup.title.get_text(strip=True) if soup.title else ""
    )
    if not page_title or "Age Verification" in page_title or "所在地區年齡檢測" in page_title:
        raise MetadataError("未找到该番号")

    title = page_title
    if title.upper().startswith(code.upper()):
        title = title[len(code) :].strip(" -–—")
    title = title.replace(" - JavBus", "").strip()

    cover = None
    big = soup.select_one("a.bigImage")
    if big and big.get("href"):
        cover = _abs(base, big["href"])
    elif big and big.find("img"):
        cover = _abs(base, big.find("img").get("src"))

    fields = _info_fields(soup, base)

    actors: list[dict] = []
    seen: set[str] = set()
    for box in soup.select("#avatar-waterfall a.avatar-box"):
        name = (box.select_one("span") or box).get_text(strip=True)
        img = box.select_one("img")
        photo = _abs(base, img.get("src") if img else "")
        if not name or name in seen:
            continue
        seen.add(name)
        actors.append({"name": name, "photo": photo, "url": _abs(base, box.get("href"))})
    if not actors:
        for link in (fields.get("演員") or {}).get("links") or []:
            if link["name"] in seen:
                continue
            seen.add(link["name"])
            actors.append({"name": link["name"], "photo": "", "url": link.get("url") or ""})

    genres: list[str] = []
    for link in (fields.get("類別") or fields.get("类别") or {}).get("links") or []:
        if link["name"] not in genres:
            genres.append(link["name"])
    if not genres:
        for a in soup.select("span.genre a"):
            href = a.get("href") or ""
            if "/star" in href:
                continue
            name = a.get_text(strip=True)
            if name and name != "多選提交" and name not in genres:
                genres.append(name)

    samples: list[dict] = []
    for a in soup.select("#sample-waterfall a.sample-box, a.sample-box"):
        img = a.find("img")
        thumb = _abs(base, img.get("src") if img else "")
        full = a.get("href") or thumb
        if not (thumb or full):
            continue
        samples.append({"thumb": thumb or full, "full": full or thumb})

    return {
        "code": code,
        "title": title,
        "cover": cover,
        "actors": actors,
        "studio": _first_link_or_text(fields.get("製作商") or fields.get("制作商")),
        "label": _first_link_or_text(fields.get("發行商") or fields.get("发行商")),
        "series": _first_link_or_text(fields.get("系列")),
        "director": _first_link_or_text(fields.get("導演") or fields.get("导演")),
        "release_date": (fields.get("發行日期") or fields.get("发行日期") or {}).get("text") or "",
        "runtime": (fields.get("長度") or fields.get("长度") or {}).get("text") or "",
        "genres": genres,
        "samples": samples,
        "source": "javbus",
        "url": _abs(base, code),
    }


def _code_from_search_box(box, href: str) -> str:
    dates = [d.get_text(strip=True) for d in box.select("date")]
    if dates:
        raw = dates[0].strip()
        return normalize_code(raw) or raw.upper()
    path = urlparse(href).path.rstrip("/").split("/")[-1]
    path = DATE_SUFFIX_RE.sub("", path)
    return normalize_code(path) or path.upper()


def parse_search(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    seen: set[str] = set()
    for box in soup.select("a.movie-box"):
        href = _abs(base, box.get("href") or "")
        if not href:
            continue
        code = _code_from_search_box(box, href)
        if not code or code in seen:
            continue
        seen.add(code)
        img = box.select_one("img")
        cover = _abs(base, img.get("src") if img else "")
        title = (img.get("title") if img else "") or ""
        if not title:
            span = box.select_one(".photo-info span")
            title = span.get_text(" ", strip=True) if span else ""
        dates = [d.get_text(strip=True) for d in box.select("date")]
        release = dates[1] if len(dates) > 1 else ""
        items.append({
            "code": code,
            "title": title.replace(code, "", 1).strip(" -–—") if title.upper().startswith(code.upper()) else title,
            "cover": cover,
            "release_date": release,
            "url": href,
            "source": "javbus",
        })
    return items


def javbus_page_kind(url: str) -> str | None:
    path = urlparse(url).path
    if "/star/" in path:
        return "actress"
    if "/series/" in path:
        return "series"
    if "/studio/" in path or "/label/" in path:
        return "studio"
    return None


def parse_star_links(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    found: list[dict] = []
    seen: set[str] = set()
    for link in soup.select("a[href*='/star/']"):
        href = _abs(base, link.get("href") or "").split("?")[0].rstrip("/")
        if "/star/" not in href or href in seen:
            continue
        image = link.select_one("img")
        name = (image.get("title") if image else "") or ""
        if not name:
            span = link.select_one("span")
            name = span.get_text(strip=True) if span else ""
        if not name:
            name = link.get_text(" ", strip=True)
        name = name.strip()
        if not name:
            continue
        seen.add(href)
        found.append({"name": name, "url": href})
    return found


async def fetch_javbus_html(settings: Settings, url: str) -> str:
    base = settings.javbus_base.rstrip("/")
    allowed = (urlparse(base).hostname or "").lower()
    host = (urlparse(url).hostname or "").lower()
    if not allowed or not host or not (host == allowed or host.endswith("." + allowed)):
        raise MetadataError("只能打开当前 JavBus 域名下的页面")
    headers = {"Cookie": AGE_COOKIE, "Referer": base + "/"}
    async with site_client(settings) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise MetadataError(f"JavBus 请求失败: {exc}") from exc
    if response.status_code >= 400:
        raise MetadataError(f"JavBus HTTP {response.status_code}", response.status_code)
    return response.text


def latest_page_url(base: str, kind: str, page: int) -> str:
    root = base.rstrip("/")
    page = max(1, int(page))
    if kind == "uncensored":
        if page == 1:
            return f"{root}/uncensored"
        return f"{root}/uncensored/page/{page}"
    if kind != "censored":
        raise MetadataError("列表类型无效")
    if page == 1:
        return f"{root}/"
    return f"{root}/page/{page}"


async def fetch_latest(settings: Settings, kind: str, page: int = 1) -> list[dict]:
    url = latest_page_url(settings.javbus_base, kind, page)
    base = settings.javbus_base.rstrip("/")
    headers = {"Cookie": AGE_COOKIE, "Referer": base + "/"}
    async with site_client(settings) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise MetadataError(f"JavBus 请求失败: {exc}") from exc
    if response.status_code == 404:
        return []
    if response.status_code >= 400:
        raise MetadataError(f"JavBus HTTP {response.status_code}", response.status_code)
    return parse_search(response.text, base)


async def search_works(settings: Settings, query: str, pages: int = 2) -> list[dict]:
    base = settings.javbus_base.rstrip("/")
    encoded = quote(query.strip())
    headers = {"Cookie": AGE_COOKIE, "Referer": base + "/"}
    collected: list[dict] = []
    seen: set[str] = set()
    last_err: Exception | None = None
    async with site_client(settings) as client:
        for page in range(1, pages + 1):
            url = f"{base}/search/{encoded}" if page == 1 else f"{base}/search/{encoded}/{page}"
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                last_err = MetadataError(f"JavBus 请求失败: {e}")
                break
            if r.status_code == 404:
                if page == 1:
                    last_err = MetadataError("没有搜到作品", 404)
                break
            if r.status_code >= 400:
                last_err = MetadataError(f"JavBus HTTP {r.status_code}", r.status_code)
                break
            for it in parse_search(r.text, base):
                if it["code"] in seen:
                    continue
                seen.add(it["code"])
                collected.append(it)
            if len(collected) >= 48:
                break
    if not collected and last_err:
        raise last_err
    return collected


async def fetch_metadata(settings: Settings, code: str) -> dict:
    base = settings.javbus_base.rstrip("/")
    headers = {"Cookie": AGE_COOKIE, "Referer": base + "/"}
    paths = [f"/{code}", f"/uncensored/{code}"]
    last_err: Exception | None = None
    async with site_client(settings) as client:
        for path in paths:
            url = base + path
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                last_err = MetadataError(f"JavBus 请求失败: {e}")
                continue
            if r.status_code == 404:
                last_err = MetadataError("未找到该番号", 404)
                continue
            if r.status_code >= 400:
                last_err = MetadataError(f"JavBus HTTP {r.status_code}", r.status_code)
                continue
            try:
                return parse_javbus(r.text, base, code)
            except MetadataError as e:
                last_err = e
                continue
    raise last_err or MetadataError("JavBus 无响应")

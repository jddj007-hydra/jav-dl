from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.codes import normalize_code
from app.config import Settings
from app.httputil import site_client

DATE_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}$")
_THUMB_RE = re.compile(r"/pics/thumb/([^/?#]+)\.(jpe?g|png|webp)(?=$|[?#])", re.I)
# These labels only release omnibus discs. List cards have no genre, and the title
# often never says ベスト. Regular labels such as SSIS / MIDA / IPZZ are not here.
_OMNIBUS_PREFIXES = frozenset({
    "OFJE", "ONSD", "IDBD", "MIZD", "HNDB", "KWBD", "IPOK", "RBB", "PBD",
    "PPBD", "KIBD",
})
_OMNIBUS_TITLE = re.compile(
    r"ベスト|best|総集|合集|全集|打包|コンプリート|complete|メモリアル|memorial|"
    r"お得セット|girls\s*collection|全\d+\s*(?:タイトル|作品)|"
    r"(?<![a-z0-9])box(?![a-z0-9])",
    re.I,
)
_OMNIBUS_COUNT = re.compile(
    r"(?:[2-9]\d|\d{3,})\s*(?:本番|連発)|"
    r"(?:[1-9]\d|\d{3,})\s*(?:タイトル|作品)|"
    r"収録|福袋|永久保存|女体図鑑|女たち|オンナたち"
)
# 8時間 / 10時間SP is a disc length. 20時間犯 is one scene, so 犯・後 stay.
_OMNIBUS_HOURS = re.compile(r"(\d+)\s*時間(?!犯|後|以内|以上|目)")
_OMNIBUS_MINUTES = re.compile(r"(\d+)\s*分")
_OMNIBUS_CAST = re.compile(
    r"(?:[5-9]|\d{2,})\s*(?:選|射精|名)|"
    r"(?:女|美女|女優|女子|女の子|ギャル|人妻|娘たち)\D{0,6}(?:[1-9]\d|\d{3,})\s*人"
)

AGE_COOKIE = "age=verified; existmag=all"
CACHE_VER = "v4"


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


def _named_link(field: dict | None) -> tuple[str, str]:
    if not field:
        return "", ""
    links = field.get("links") or []
    if links:
        return links[0]["name"], links[0].get("url") or ""
    return (field.get("text") or "").strip(), ""


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

    genres = _genres(soup, fields, base)

    studio, studio_url = _named_link(fields.get("製作商") or fields.get("制作商"))
    label, label_url = _named_link(fields.get("發行商") or fields.get("发行商"))
    series, series_url = _named_link(fields.get("系列"))

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
        "studio": studio,
        "studio_url": studio_url,
        "label": label,
        "label_url": label_url,
        "series": series,
        "series_url": series_url,
        "director": _first_link_or_text(fields.get("導演") or fields.get("导演")),
        "release_date": (fields.get("發行日期") or fields.get("发行日期") or {}).get("text") or "",
        "runtime": (fields.get("長度") or fields.get("长度") or {}).get("text") or "",
        "genres": genres,
        "samples": samples,
        "source": "javbus",
        "url": _abs(base, code),
    }


def listing_cover(url: str) -> str:
    """List pages often publish a dead /pics/thumb file. The cover JPEG is the real image."""
    if not url:
        return ""

    def repl(match: re.Match) -> str:
        ext = match.group(2).lower()
        if ext == "jpeg":
            ext = "jpg"
        return f"/pics/cover/{match.group(1)}_b.{ext}"

    return _THUMB_RE.sub(repl, url, count=1)


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
        cover = listing_cover(_abs(base, img.get("src") if img else ""))
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


def _genres(soup, fields: dict, base: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def add(name: str, url: str) -> None:
        name = (name or "").strip()
        if not name or name == "多選提交" or name in seen:
            return
        seen.add(name)
        found.append({"name": name, "url": url or ""})

    for link in (fields.get("類別") or fields.get("类别") or {}).get("links") or []:
        add(link.get("name") or "", link.get("url") or "")
    if found:
        return found
    for a in soup.select("span.genre a"):
        href = a.get("href") or ""
        if "/star" in href:
            continue
        add(a.get_text(strip=True), _abs(base, href))
    return found


def is_omnibus_work(item: dict) -> bool:
    """Actress pages list every credit, so publisher omnibus discs crowd out her own titles."""
    code = (item.get("code") or "").upper()
    prefix = code.split("-", 1)[0]
    if prefix in _OMNIBUS_PREFIXES:
        return True
    title = item.get("title") or ""
    if _OMNIBUS_TITLE.search(title) or _OMNIBUS_COUNT.search(title) or _OMNIBUS_CAST.search(title):
        return True
    hours = [int(n) for n in _OMNIBUS_HOURS.findall(title)]
    if any(n >= 4 for n in hours):
        return True
    minutes = [int(n) for n in _OMNIBUS_MINUTES.findall(title)]
    return any(n >= 360 for n in minutes)


def javbus_page_kind(url: str) -> str | None:
    path = urlparse(url).path
    if "/star/" in path:
        return "actress"
    if "/series/" in path:
        return "series"
    if "/studio/" in path or "/label/" in path:
        return "studio"
    if "/genre/" in path:
        return "genre"
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


def latest_page_url(base: str, kind: str, page: int, fmt: str = "flat") -> str:
    root = base.rstrip("/")
    page = max(1, int(page))
    if kind not in ("censored", "uncensored"):
        raise MetadataError("列表类型无效")
    if (fmt or "flat").strip().lower() == "vr":
        path = "uncensored/genre/gre162" if kind == "uncensored" else "genre/7x"
        if page == 1:
            return f"{root}/{path}"
        return f"{root}/{path}/{page}"
    if kind == "uncensored":
        if page == 1:
            return f"{root}/uncensored"
        return f"{root}/uncensored/page/{page}"
    if page == 1:
        return f"{root}/"
    return f"{root}/page/{page}"


async def fetch_latest(settings: Settings, kind: str, page: int = 1, fmt: str = "flat") -> list[dict]:
    url = latest_page_url(settings.javbus_base, kind, page, fmt)
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
    from app.codes import fc2_number
    from app.sources.fc2 import fetch_fc2_metadata

    # JavBus 没有 FC2 详情页；番号是 FC2 时直接走官方商品页。
    if fc2_number(code):
        return await fetch_fc2_metadata(settings, code)

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

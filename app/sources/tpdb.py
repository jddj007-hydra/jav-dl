from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import Settings
from app.httputil import site_client
from app.studios import compact_site as _compact_site
from app.studios import is_vr_work, pick_site
from app.western_magnets import parse_release_date, release_text, split_release_name, words

API_BASE = "https://api.theporndb.net"
# 男同 / 双性恋 / 双性人。标签名来自 ThePornDB，标题里的中文留给 JavBus。
_EXCLUDED_ORIENTATION = re.compile(
    r"\bgay\b|\bbisexual\b|\bfutanari\b|\bshemale\b|\bintersex\b|\btransgender\b|\btrans\b|trans-|男同|双性|变性|ゲイ",
    re.I,
)


def is_excluded_orientation(tags: list[str] | None = None, title: str = "") -> bool:
    blob = " ".join([title or "", *(tags or [])])
    return bool(_EXCLUDED_ORIENTATION.search(blob))


# 42 tiles the desktop wall: 7 columns × 6 rows, or 6 columns × 7.
PER_PAGE = 42
ORDER_LATEST = "recently_released"
ORDER_SEARCH = "recently_released"
# Trailers, BTS, and clip-site shorts sit under this. On /scenes, unset duration
# is treated as short. Site browse cannot ask ThePornDB for duration (SexLikeReal
# and other VR dumps leave it empty), so those lists keep unknown length.
MIN_DURATION_SECONDS = 15 * 60
MIN_DURATION_MINUTES = MIN_DURATION_SECONDS // 60
# slug, ThePornDB tag id, tag name, Chinese label
THEMES: dict[str, tuple[str, str, str]] = {
    "blowjob": ("29", "Blowjob", "口交"),
    "anal": ("70", "Anal", "肛交"),
    "creampie": ("88", "Creampie", "内射"),
    "threesome": ("127", "Threesome", "3P"),
    "lesbian": ("8", "Lesbian", "女同"),
    "milf": ("126598", "MILF (30+)", "熟女"),
    "tits": ("95976", "Big Tits", "巨乳"),
    "pov": ("19581", "POV", "POV"),
    "gangbang": ("66", "Gangbang", "群交"),
    "bbc": ("11891", "BBC", "黑人"),
    "facial": ("25", "Facial", "颜射"),
    "massage": ("135", "Massage", "按摩"),
    "schoolgirl": ("787", "Schoolgirl", "制服"),
}
VR_TAG = ("503", "Virtual Reality")


_TAIL_JUNK = re.compile(
    r"^(?:xxx|1080p|2160p|720p|480p|4k|mp4|mkv|avi|wmv|x264|x265|h264|h265|hevc|webrip|webdl|web-dl|bluray|hdr)$",
    re.I,
)


class TpdbError(Exception):
    pass


def duration_minutes(duration: object) -> str:
    try:
        seconds = int(duration)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return str(max(1, seconds // 60))


def is_too_short(
    duration_minutes_text: object,
    minimum: int = MIN_DURATION_MINUTES,
    *,
    allow_unknown: bool = False,
) -> bool:
    raw = str(duration_minutes_text or "").strip()
    if not raw:
        return not allow_unknown
    try:
        return int(raw) < minimum
    except ValueError:
        return True


def theme_tag(theme: str | None) -> tuple[str, str] | None:
    key = (theme or "").strip().lower()
    if not key:
        return None
    row = THEMES.get(key)
    if row is None:
        raise TpdbError("没有这个题材")
    return row[0], row[1]


def list_params(page: int, query: str | None = None, theme: str | None = None, fmt: str | None = None) -> dict:
    params: dict = {
        "page": max(1, int(page)),
        "per_page": PER_PAGE,
        "orderBy": ORDER_SEARCH if query else ORDER_LATEST,
        "duration": MIN_DURATION_SECONDS,
        "duration_operation": ">=",
    }
    if query:
        params["q"] = query
    tag = theme_tag(theme)
    if tag:
        tag_id, tag_name = tag
        params[f"tags[{tag_id}]"] = tag_name
    if (fmt or "").strip().lower() == "vr":
        params[f"tags[{VR_TAG[0]}]"] = VR_TAG[1]
    return params


def _site_name(raw: dict) -> str:
    site = raw.get("site")
    if isinstance(site, dict):
        return str(site.get("name") or "")
    return ""


def _performers(raw: dict) -> list[str]:
    names: list[str] = []
    for performer in raw.get("performers") or []:
        if not isinstance(performer, dict):
            continue
        parent = performer.get("parent")
        source = parent if isinstance(parent, dict) and parent.get("name") else performer
        name = str(source.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _tag_refs(raw: dict) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
            ident = str(tag.get("id") or "").strip()
        else:
            name = str(tag or "").strip()
            ident = ""
        if not name or name in seen:
            continue
        seen.add(name)
        refs.append({"id": ident, "name": name})
    return refs


def map_item(raw: dict, kind: str) -> dict:
    posters = raw.get("posters") if isinstance(raw.get("posters"), dict) else {}
    background = raw.get("background") if isinstance(raw.get("background"), dict) else {}
    cover = posters.get("large") or posters.get("medium") or posters.get("small") or ""
    if not cover:
        cover = str(raw.get("image") or raw.get("poster") or "")
    bg = ""
    if isinstance(raw.get("background"), str):
        bg = raw.get("background")
    else:
        bg = str(background.get("full") or background.get("large") or "")
    tags = _tag_refs(raw)
    site = _site_name(raw)
    title = str(raw.get("title") or "")
    tag_names = [ref["name"] for ref in tags]
    return {
        "id": str(raw.get("id") or ""),
        "kind": kind,
        "title": title,
        "date": str(raw.get("date") or "")[:10],
        "site": site,
        "performers": _performers(raw),
        "cover": str(cover or ""),
        "background": bg,
        "description": str(raw.get("description") or ""),
        "tags": tag_names,
        "tag_refs": tags,
        "duration": duration_minutes(raw.get("duration")),
        "url": str(raw.get("url") or ""),
        "vr": is_vr_work(site, tag_names, title),
    }


def list_path(kind: str) -> str:
    if kind == "scene":
        return "/scenes"
    if kind == "movie":
        return "/movies"
    raise TpdbError("列表类型无效")


async def _get_json(settings: Settings, path: str, params: dict | None = None) -> dict:
    token = (settings.tpdb_api_key or "").strip()
    if not token:
        raise TpdbError("请先在设置里填写 ThePornDB token")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "jav-dl",
    }
    async with site_client(settings) as client:
        try:
            response = await client.get(API_BASE + path, params=params or None, headers=headers)
        except httpx.HTTPError as exc:
            raise TpdbError(f"ThePornDB 请求失败: {exc}") from exc
    if response.status_code == 401:
        raise TpdbError("ThePornDB token 无效")
    if response.status_code >= 400:
        raise TpdbError(f"ThePornDB HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise TpdbError("ThePornDB 响应无法解析") from exc
    if not isinstance(payload, dict):
        raise TpdbError("ThePornDB 响应无法解析")
    if payload.get("error"):
        raise TpdbError(str(payload["error"]))
    return payload


def _scene_fold_key(item: dict) -> tuple[str, str, str] | None:
    title = (item.get("title") or "").casefold().strip()
    if not title:
        return None
    date = (item.get("date") or "")[:10]
    return (title, date, _compact_site(item.get("site") or ""))


def _duration_score(item: dict) -> int:
    try:
        return int(item.get("duration") or 0)
    except (TypeError, ValueError):
        return 0


def _unpack_list(payload: dict, kind: str, page: int) -> dict:
    data = payload.get("data")
    if not isinstance(data, list):
        data = []
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    try:
        last_page = int(meta.get("last_page") or page)
    except (TypeError, ValueError):
        last_page = page
    items: list[dict] = []
    seen_ids: set[str] = set()
    by_key: dict[tuple[str, str, str], int] = {}
    for row in data:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ident = str(row["id"])
        if ident in seen_ids:
            continue
        item = map_item(row, kind)
        key = _scene_fold_key(item)
        if key and key in by_key:
            idx = by_key[key]
            if _duration_score(item) > _duration_score(items[idx]):
                seen_ids.discard(str(items[idx].get("id") or ""))
                seen_ids.add(ident)
                items[idx] = item
            continue
        seen_ids.add(ident)
        if key:
            by_key[key] = len(items)
        items.append(item)
    return {"items": items, "page": page, "last_page": max(last_page, page)}


async def match_site(settings: Settings, query: str) -> dict | None:
    query = (query or "").strip()
    if len(_compact_site(query)) < 5:
        return None
    payload = await _get_json(settings, "/sites", {"q": query, "per_page": 50})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    return pick_site(rows, query)


async def match_performer(settings: Settings, query: str) -> dict | None:
    query = (query or "").strip()
    if len(query) < 3:
        return None
    payload = await _get_json(settings, "/performers", {"q": query, "per_page": 50})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    return _exact_name(rows, query)


async def search_catalog(
    settings: Settings,
    kind: str,
    query: str,
    page: int = 1,
    theme: str | None = None,
    fmt: str | None = None,
) -> dict:
    """Studio or performer names open that list. A title still goes through keyword search."""
    query = (query or "").strip()
    page = max(1, int(page))
    if query and not theme:
        site = await match_site(settings, query)
        if site and site.get("name"):
            payload = await fetch_facet(settings, kind, "site", str(site["name"]), page)
            payload["matched_site"] = str(site["name"])
            return payload
        performer = await match_performer(settings, query)
        if performer and performer.get("name"):
            payload = await fetch_facet(settings, kind, "performer", str(performer["name"]), page)
            payload["matched_performer"] = str(performer["name"])
            return payload
    return await fetch_list(settings, kind, page, query or None, theme, fmt=fmt)


async def fetch_list(
    settings: Settings,
    kind: str,
    page: int = 1,
    query: str | None = None,
    theme: str | None = None,
    extra: dict | None = None,
    fmt: str | None = None,
) -> dict:
    page = max(1, int(page))
    params = list_params(page, query, theme, fmt)
    if extra:
        params.update(extra)
    payload = await _get_json(settings, list_path(kind), params)
    return _unpack_list(payload, kind, page)


def _exact_name(rows: list, name: str) -> dict | None:
    wanted = name.casefold()
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if str(row.get("name") or "").casefold() == wanted:
            return row
    return None


async def _named_id(settings: Settings, path: str, name: str) -> str:
    payload = await _get_json(settings, path, {"q": name, "per_page": 50})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    match = _exact_name(rows, name)
    if not match and path == "/sites":
        match = pick_site(rows, name)
    if not match:
        raise TpdbError(f"没有找到 {name}")
    return str(match["id"])


async def catalog_id(settings: Settings, kind: str, name: str) -> str:
    path = "/performers" if kind == "western_performer" else "/sites"
    return await _named_id(settings, path, name)


async def _scenes(settings: Settings, extra: dict, page: int) -> list[dict]:
    payload = await _get_json(settings, "/scenes", {**list_params(page), **extra})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    return [map_item(row, "scene") for row in rows if isinstance(row, dict) and row.get("id")]


async def _named_tag(settings: Settings, name: str) -> tuple[str, str]:
    payload = await _get_json(settings, "/tags", {"q": name, "per_page": 30})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    match = _exact_name(rows, name)
    if not match or not str(match.get("id") or "").strip():
        raise TpdbError(f"没有找到 {name}")
    return str(match["id"]), str(match.get("name") or name)


async def fetch_facet(
    settings: Settings,
    kind: str,
    facet: str,
    name: str,
    page: int = 1,
    tag_id: str = "",
) -> dict:
    """Works for one performer, site, or tag, matched by the exact catalog name."""
    facet = (facet or "").strip().lower()
    if facet not in ("performer", "site", "tag"):
        raise TpdbError("类型无效")
    name = (name or "").strip()
    if not name:
        raise TpdbError("请填写名字")
    page = max(1, int(page))
    if facet == "tag":
        ident = (tag_id or "").strip()
        if not ident.isdigit():
            ident, name = await _named_tag(settings, name)
        params = list_params(page)
        params[f"tags[{ident}]"] = name
        payload = await _get_json(settings, list_path(kind), params)
        return _unpack_list(payload, kind, page)
    root = "/performers" if facet == "performer" else "/sites"
    ident = await _named_id(settings, root, name)
    leaf = list_path(kind).strip("/")
    path = f"{root}/{quote(ident, safe='')}/{leaf}"
    payload = await _get_json(settings, path, {"page": page, "per_page": PER_PAGE})
    return _unpack_list(payload, kind, page)


async def scenes_for_performer(
    settings: Settings,
    name: str,
    page: int = 1,
    performer_id: str | None = None,
) -> list[dict]:
    performer_id = performer_id or await _named_id(settings, "/performers", name)
    return await _scenes(settings, {"performers": performer_id}, page)


async def scenes_for_site(
    settings: Settings,
    name: str,
    page: int = 1,
    site_id: str | None = None,
) -> list[dict]:
    site_id = site_id or await _named_id(settings, "/sites", name)
    return await _scenes(settings, {"sites": site_id}, page)


_VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".wmv", ".ts", ".mov", ".m4v", ".webm"}
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_JUNK = re.compile(
    r"(?i)(?:^|[.\s_-])(?:xxx|1080p|2160p|720p|480p|4k|mp4|mkv|avi|wmv|x264|x265|h264|h265|hevc|webrip|webdl|web-dl|bluray|hdr)(?=$|[.\s_-])"
)


def _add_query(queries: list[str], item: str) -> None:
    item = (item or "").strip(" .")
    if item and item not in queries and len(queries) < 16:
        queries.append(item)


def _cleaned_forms(name: str) -> tuple[str, str]:
    bare = re.sub(r"\s+", " ", _BRACKETS.sub(" ", name)).strip(" .")
    cleaned = re.sub(r"[.\s_-]+", " ", _JUNK.sub(" ", bare)).strip()
    dotted = re.sub(r"\s+", ".", cleaned)
    return cleaned, dotted


def filename_queries(filename: str) -> list[str]:
    """Release name, then the same name without groups, quality tags, and the scene title.

    ThePornDB parse misses [GROUP], a trailing 1080p, a -TRB group, or a title
    that does not match the catalog title. Site.YY.MM.DD plus the performer still hits.
    """
    name = (filename or "").strip()
    if Path(name).suffix.lower() in _VIDEO_SUFFIXES:
        name = Path(name).stem
    queries: list[str] = []
    bare = re.sub(r"\s+", " ", _BRACKETS.sub(" ", name)).strip(" .")
    cleaned, dotted = _cleaned_forms(name)
    for item in (name, bare, cleaned, dotted):
        _add_query(queries, item)
    core = release_text(filename)
    if core:
        core_cleaned, core_dotted = _cleaned_forms(core)
        for item in (core, core_cleaned, core_dotted):
            _add_query(queries, item)
        split = split_release_name(core_dotted or core_cleaned or core)
        if split:
            head, tail = split
            useful = [tok for tok in tail if len(tok) > 1 and not _TAIL_JUNK.match(tok)]
            if useful:
                _add_query(queries, ".".join([head, *useful]))
            for size in (4, 3, 2, 1):
                if size >= len(useful):
                    continue
                piece = ".".join([head, *useful[:size]])
                _add_query(queries, piece.replace(".", " "))
                _add_query(queries, piece)
    return queries


def _dates_close(filename: str, item_date: str) -> bool:
    wanted = parse_release_date(filename)
    got = (item_date or "")[:10]
    if not wanted or not got:
        return True
    try:
        left = date.fromisoformat(wanted)
        right = date.fromisoformat(got)
    except ValueError:
        return True
    return abs((left - right).days) <= 1


def choose_match(rows: list, kind: str, filename: str) -> dict | None:
    items = [map_item(row, kind) for row in rows if isinstance(row, dict) and row.get("id")]
    items = [item for item in items if _dates_close(filename, item.get("date") or "")]
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    wanted = {word.lower() for word in words(filename)}
    scored: list[tuple[int, dict]] = []
    for item in items:
        blob = " ".join([
            item.get("title") or "",
            item.get("site") or "",
            *(item.get("performers") or []),
        ])
        have = {word.lower() for word in words(blob)}
        scored.append((len(wanted & have), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best = scored[0][0]
    second = scored[1][0]
    if best >= 2 and best > second:
        return scored[0][1]
    return None


async def _parsed_item(settings: Settings, kind: str, query: str, filename: str) -> dict | None:
    payload = await _get_json(settings, list_path(kind), {"parse": query})
    data = payload.get("data")
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict):
        rows = [data]
    return choose_match(rows, kind, filename)


async def fetch_by_filename(settings: Settings, filename: str) -> dict:
    """Match a Site.YY.MM.DD release name. Scenes first, then movies."""
    queries = filename_queries(filename)
    if not queries:
        raise TpdbError("没有可解析的文件名")
    for query in queries:
        for kind in ("scene", "movie"):
            item = await _parsed_item(settings, kind, query, filename)
            if item:
                return item
    raise TpdbError("没有匹配的欧美作品")


async def fetch_detail(settings: Settings, kind: str, item_id: str) -> dict:
    payload = await _get_json(settings, f"{list_path(kind)}/{item_id}")
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("id"):
        raise TpdbError("没有这条作品")
    return map_item(data, kind)

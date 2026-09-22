from __future__ import annotations

import httpx

from app.config import Settings
from app.httputil import site_client

API_BASE = "https://api.theporndb.net"
PER_PAGE = 24
ORDER_LATEST = "recently_released"
ORDER_SEARCH = "most_relevant"


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


def _tags(raw: dict) -> list[str]:
    tags: list[str] = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
        else:
            name = str(tag or "").strip()
        if name and name not in tags:
            tags.append(name)
    return tags


def map_item(raw: dict, kind: str) -> dict:
    posters = raw.get("posters") if isinstance(raw.get("posters"), dict) else {}
    background = raw.get("background") if isinstance(raw.get("background"), dict) else {}
    cover = posters.get("large") or posters.get("medium") or posters.get("small") or ""
    return {
        "id": str(raw.get("id") or ""),
        "kind": kind,
        "title": str(raw.get("title") or ""),
        "date": str(raw.get("date") or "")[:10],
        "site": _site_name(raw),
        "performers": _performers(raw),
        "cover": str(cover or ""),
        "background": str(background.get("full") or ""),
        "description": str(raw.get("description") or ""),
        "tags": _tags(raw),
        "duration": duration_minutes(raw.get("duration")),
        "url": str(raw.get("url") or ""),
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


async def fetch_list(
    settings: Settings,
    kind: str,
    page: int = 1,
    query: str | None = None,
) -> dict:
    page = max(1, int(page))
    params: dict = {
        "page": page,
        "per_page": PER_PAGE,
        "orderBy": ORDER_SEARCH if query else ORDER_LATEST,
    }
    if query:
        params["q"] = query
    payload = await _get_json(settings, list_path(kind), params)
    data = payload.get("data")
    if not isinstance(data, list):
        data = []
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    try:
        last_page = int(meta.get("last_page") or page)
    except (TypeError, ValueError):
        last_page = page
    items = [
        map_item(row, kind)
        for row in data
        if isinstance(row, dict) and row.get("id")
    ]
    return {"items": items, "page": page, "last_page": max(last_page, page)}


async def fetch_detail(settings: Settings, kind: str, item_id: str) -> dict:
    payload = await _get_json(settings, f"{list_path(kind)}/{item_id}")
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("id"):
        raise TpdbError("没有这条作品")
    return map_item(data, kind)

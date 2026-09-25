from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.sources.tpdb import (
    TpdbError,
    fetch_detail,
    fetch_list,
    is_excluded_orientation,
    is_too_short,
    theme_tag,
)

router = APIRouter()


def _kind(kind: str) -> str:
    if kind not in ("scene", "movie"):
        raise HTTPException(400, "列表类型无效")
    return kind


def _theme(theme: str) -> str | None:
    theme = (theme or "").strip().lower()
    if not theme:
        return None
    try:
        theme_tag(theme)
    except TpdbError as exc:
        raise HTTPException(400, str(exc)) from exc
    return theme


def _visible(items: list[dict], *, latest: bool) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        if is_too_short(item.get("duration")):
            continue
        if latest and is_excluded_orientation(item.get("tags") or [], item.get("title") or ""):
            continue
        kept.append(item)
    return kept


async def _cached_list(
    request: Request,
    kind: str,
    page: int,
    query: str | None,
    theme: str | None = None,
    *,
    latest: bool = False,
):
    settings = request.app.state.settings
    if not (settings.tpdb_api_key or "").strip():
        return {
            "kind": kind,
            "page": page,
            "last_page": page,
            "items": [],
            "error": "请先在设置里填写 ThePornDB token",
        }
    db = request.app.state.db
    cache_key = None if query else f"latest:tpdb:{kind}:{page}:{theme or '-'}"
    if cache_key:
        cached = await db.get_metadata(cache_key, settings.latest_ttl)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return {**cached, "items": _visible(cached["items"], latest=latest), "error": None}
    try:
        payload = await fetch_list(settings, kind, page, query, theme)
    except TpdbError as exc:
        return {
            "kind": kind,
            "page": page,
            "last_page": page,
            "items": [],
            "error": str(exc),
        }
    body = {
        "kind": kind,
        "page": payload["page"],
        "last_page": payload["last_page"],
        "items": payload["items"],
    }
    if cache_key:
        await db.put_metadata(cache_key, body)
    return {**body, "items": _visible(body["items"], latest=latest), "error": None}


@router.get("/api/western/latest")
async def western_latest(
    request: Request,
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
    theme: str = Query(""),
):
    return await _cached_list(request, _kind(kind), page, None, _theme(theme), latest=True)


@router.get("/api/western/search")
async def western_search(
    request: Request,
    q: str = Query(""),
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
    theme: str = Query(""),
):
    query = q.strip()
    if not query:
        raise HTTPException(400, "请输入片名或演员")
    return await _cached_list(request, _kind(kind), page, query, _theme(theme))


@router.get("/api/western/{kind}/{item_id}")
async def western_detail(request: Request, kind: str, item_id: str):
    kind = _kind(kind)
    item_id = item_id.strip()
    if not item_id:
        raise HTTPException(400, "缺少作品 id")
    settings = request.app.state.settings
    if not (settings.tpdb_api_key or "").strip():
        return {"item": None, "error": "请先在设置里填写 ThePornDB token", "cached": False}
    db = request.app.state.db
    key = f"tpdb:{kind}:{item_id}"
    cached = await db.get_metadata(key, settings.metadata_ttl)
    if isinstance(cached, dict) and cached.get("id"):
        return {"item": cached, "error": None, "cached": True}
    try:
        item = await fetch_detail(settings, kind, item_id)
    except TpdbError as exc:
        return {"item": None, "error": str(exc), "cached": False}
    await db.put_metadata(key, item)
    return {"item": item, "error": None, "cached": False}

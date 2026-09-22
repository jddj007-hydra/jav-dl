from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.sources.tpdb import TpdbError, fetch_detail, fetch_list

router = APIRouter()


def _kind(kind: str) -> str:
    if kind not in ("scene", "movie"):
        raise HTTPException(400, "列表类型无效")
    return kind


async def _cached_list(request: Request, kind: str, page: int, query: str | None):
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
    cache_key = None if query else f"latest:tpdb:{kind}:{page}"
    if cache_key:
        cached = await db.get_metadata(cache_key, settings.latest_ttl)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return {**cached, "error": None}
    try:
        payload = await fetch_list(settings, kind, page, query)
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
    return {**body, "error": None}


@router.get("/api/western/latest")
async def western_latest(
    request: Request,
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
):
    return await _cached_list(request, _kind(kind), page, None)


@router.get("/api/western/search")
async def western_search(
    request: Request,
    q: str = Query(""),
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
):
    query = q.strip()
    if not query:
        raise HTTPException(400, "请输入片名或演员")
    return await _cached_list(request, _kind(kind), page, query)


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

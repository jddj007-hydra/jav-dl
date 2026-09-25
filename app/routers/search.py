from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import normalize_code
from app.library import attach_library
from app.sources.javbus import CACHE_VER, MetadataError, fetch_latest, fetch_metadata, search_works
from app.sources.tpdb import is_excluded_orientation

router = APIRouter()


@router.get("/api/jav/latest")
async def jav_latest(
    request: Request,
    kind: str = Query("censored"),
    page: int = Query(1, ge=1, le=50),
):
    if kind not in ("censored", "uncensored"):
        raise HTTPException(400, "列表类型无效")
    settings = request.app.state.settings
    db = request.app.state.db
    library = request.app.state.library
    key = f"latest:javbus:{kind}:{page}"
    cached = await db.get_metadata(key, settings.latest_ttl)
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        items = cached["items"]
    else:
        try:
            items = await fetch_latest(settings, kind, page)
        except MetadataError as exc:
            return {"kind": kind, "page": page, "items": [], "error": str(exc)}
        await db.put_metadata(key, {"kind": kind, "page": page, "items": items})
    items = [it for it in items if not is_excluded_orientation([], it.get("title") or "")]
    hits = await library.get_many([it["code"] for it in items if it.get("code")])
    return {
        "kind": kind,
        "page": page,
        "items": attach_library(items, hits),
        "error": None,
    }


@router.get("/api/search")
async def search(request: Request, q: str | None = Query(None), code: str | None = Query(None)):
    raw = (q or code or "").strip()
    if not raw:
        raise HTTPException(400, "请输入番号或关键词")
    normalized = normalize_code(raw)
    settings = request.app.state.settings
    library = request.app.state.library
    if not normalized:
        try:
            items = await search_works(settings, raw)
        except MetadataError as e:
            return {"mode": "keyword", "query": raw, "items": [], "error": str(e)}
        hits = await library.get_many([it["code"] for it in items])
        return {
            "mode": "keyword",
            "query": raw,
            "items": attach_library(items, hits),
            "error": None if items else "没有搜到作品",
        }

    db = request.app.state.db
    cache_key = f"{CACHE_VER}:{normalized}"
    lib = await library.info_for(normalized)
    cached = await db.get_metadata(cache_key, settings.metadata_ttl)
    if cached:
        return {
            "mode": "code",
            "code": normalized,
            "metadata": cached,
            "error": None,
            "cached": True,
            "library": lib,
        }
    try:
        meta = await fetch_metadata(settings, normalized)
    except MetadataError as e:
        return {
            "mode": "code",
            "code": normalized,
            "metadata": None,
            "error": str(e),
            "cached": False,
            "library": lib,
        }
    await db.put_metadata(cache_key, meta)
    return {
        "mode": "code",
        "code": normalized,
        "metadata": meta,
        "error": None,
        "cached": False,
        "library": lib,
    }

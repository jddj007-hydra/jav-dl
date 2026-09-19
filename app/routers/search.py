from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import normalize_code
from app.sources.javbus import CACHE_VER, MetadataError, fetch_metadata, search_works

router = APIRouter()


@router.get("/api/search")
async def search(request: Request, q: str | None = Query(None), code: str | None = Query(None)):
    raw = (q or code or "").strip()
    if not raw:
        raise HTTPException(400, "请输入番号或关键词")
    normalized = normalize_code(raw)
    settings = request.app.state.settings
    if not normalized:
        try:
            items = await search_works(settings, raw)
        except MetadataError as e:
            return {"mode": "keyword", "query": raw, "items": [], "error": str(e)}
        return {
            "mode": "keyword",
            "query": raw,
            "items": items,
            "error": None if items else "没有搜到作品",
        }

    db = request.app.state.db
    cache_key = f"{CACHE_VER}:{normalized}"
    cached = await db.get_metadata(cache_key, settings.metadata_ttl)
    if cached:
        return {"mode": "code", "code": normalized, "metadata": cached, "error": None, "cached": True}
    try:
        meta = await fetch_metadata(settings, normalized)
    except MetadataError as e:
        return {"mode": "code", "code": normalized, "metadata": None, "error": str(e), "cached": False}
    await db.put_metadata(cache_key, meta)
    return {"mode": "code", "code": normalized, "metadata": meta, "error": None, "cached": False}

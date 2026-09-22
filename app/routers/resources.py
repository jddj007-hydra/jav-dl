from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import normalize_code
from app.ranking import sort_by_heat, sort_resources
from app.sources.clm import MagnetSearchError, search_magnets

router = APIRouter()


@router.get("/api/resources")
async def resources(
    request: Request,
    code: str | None = Query(None),
    q: str | None = Query(None),
):
    settings = request.app.state.settings
    raw_code = (code or "").strip()
    if raw_code:
        normalized = normalize_code(raw_code)
        if not normalized:
            raise HTTPException(400, "番号格式无效，例如 SSIS-001")
        try:
            items = await search_magnets(settings, normalized)
        except MagnetSearchError as e:
            return {"code": normalized, "items": [], "error": str(e)}
        return {"code": normalized, "items": sort_resources(items, normalized), "error": None}
    query = (q or "").strip()
    if not query:
        raise HTTPException(400, "请输入番号或关键词")
    try:
        items = await search_magnets(settings, query)
    except MagnetSearchError as e:
        return {"query": query, "items": [], "error": str(e)}
    return {"query": query, "items": sort_by_heat(items), "error": None}

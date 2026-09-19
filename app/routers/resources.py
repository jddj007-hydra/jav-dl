from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import normalize_code
from app.ranking import sort_resources
from app.sources.clm import MagnetSearchError, search_magnets

router = APIRouter()


@router.get("/api/resources")
async def resources(request: Request, code: str = Query(...)):
    normalized = normalize_code(code)
    if not normalized:
        raise HTTPException(400, "番号格式无效，例如 SSIS-001")
    settings = request.app.state.settings
    try:
        items = await search_magnets(settings, normalized)
    except MagnetSearchError as e:
        return {"code": normalized, "items": [], "error": str(e)}
    ranked = sort_resources(items, normalized)
    return {"code": normalized, "items": ranked, "error": None}

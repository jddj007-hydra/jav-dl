from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/library")
async def library_page(request: Request):
    settings = request.app.state.settings
    jav = await request.app.state.db.list_library()
    western = await request.app.state.db.list_western()
    months: dict[str, list[dict]] = {}
    for row in jav:
        rel = row.get("path") or ""
        months.setdefault(row.get("month") or "", []).append({
            "code": row.get("code") or "",
            "path": rel,
            "full_path": str(settings.media_dir / rel) if rel else str(settings.media_dir),
            "has_nfo": bool(row.get("has_nfo")),
            "has_poster": bool(row.get("has_poster")),
        })
    jav_groups = []
    for month in sorted(months, reverse=True):
        jav_groups.append({
            "month": month,
            "items": sorted(months[month], key=lambda item: item["code"]),
        })
    studios: dict[str, list[dict]] = {}
    root = settings.western_root
    for row in western:
        rel = row.get("path") or ""
        studios.setdefault(row.get("studio") or "", []).append({
            "title": row.get("title") or "",
            "tpdb_id": row.get("tpdb_id") or "",
            "path": rel,
            "full_path": str(root / rel) if root and rel else rel,
            "has_nfo": bool(row.get("has_nfo")),
            "has_poster": bool(row.get("has_poster")),
        })
    western_groups = []
    for studio in sorted(studios):
        western_groups.append({
            "studio": studio,
            "items": sorted(studios[studio], key=lambda item: item["title"].lower()),
        })
    return {
        "jav": jav_groups,
        "western": western_groups,
        "jav_root": str(settings.media_dir),
        "western_root": str(root) if root else "",
    }

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.library import poster_file
from app.models import SuckMark
from app.suck import mark_work, parse_suck

router = APIRouter()


def _actors(raw) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(name) for name in value if name] if isinstance(value, list) else []


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
            "title": row.get("title") or "",
            "actors": _actors(row.get("actors")),
            "release_date": row.get("release_date") or "",
            "added_at": row.get("added_at") or 0,
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
            "actors": _actors(row.get("actors")),
            "release_date": row.get("release_date") or "",
            "added_at": row.get("added_at") or 0,
        })
    western_groups = []
    for studio in sorted(studios):
        western_groups.append({
            "studio": studio,
            "items": sorted(studios[studio], key=lambda item: item["title"].lower()),
        })
    suck = [
        {
            "kind": row.get("kind") or "",
            "key": row.get("key") or "",
            "title": row.get("title") or "",
            "created_at": row.get("created_at") or 0,
        }
        for row in await request.app.state.db.list_suck()
    ]
    return {
        "jav": jav_groups,
        "western": western_groups,
        "suck": suck,
        "jav_root": str(settings.media_dir),
        "western_root": str(root) if root else "",
    }


@router.post("/api/suck")
async def mark_suck(request: Request, body: SuckMark):
    try:
        item = await mark_work(
            request.app.state.db,
            request.app.state.settings,
            kind=body.kind,
            key=body.key,
            title=body.title,
            remove=body.remove,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"item": item}


@router.delete("/api/suck")
async def clear_suck(request: Request, body: SuckMark):
    try:
        kind, key = parse_suck(body.kind, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not await request.app.state.db.clear_suck(kind, key):
        raise HTTPException(404, "没有这个标记")
    return {"ok": True}


@router.get("/api/library/poster")
async def library_poster(request: Request, path: str = Query(...), kind: str = Query("jav")):
    settings = request.app.state.settings
    root = settings.western_root if kind == "western" else settings.media_dir
    found = poster_file(root, path, "western" if kind == "western" else "jav")
    if found is None:
        raise HTTPException(404, "没有封面")
    return FileResponse(found, headers={"Cache-Control": "private, max-age=86400"})

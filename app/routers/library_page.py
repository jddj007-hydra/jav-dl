from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.codes import is_jav_vr_entry
from app.library import archived_jav_path, poster_file
from app.models import SuckMark
from app.suck import mark_work, parse_suck
from app.western_archive import western_fs_rel

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
    vr_months: dict[str, list[dict]] = {}
    for row in jav:
        rel = row.get("path") or ""
        item = {
            "code": row.get("code") or "",
            "path": rel,
            "full_path": str(archived_jav_path(settings, rel)),
            "has_nfo": bool(row.get("has_nfo")),
            "has_poster": bool(row.get("has_poster")),
            "title": row.get("title") or "",
            "actors": _actors(row.get("actors")),
            "release_date": row.get("release_date") or "",
            "added_at": row.get("added_at") or 0,
        }
        bucket = vr_months if is_jav_vr_entry(row.get("code") or "", row.get("month") or "") else months
        bucket.setdefault(row.get("month") or "", []).append(item)
    jav_groups = []
    for month in sorted(months, reverse=True):
        jav_groups.append({
            "month": month,
            "items": sorted(months[month], key=lambda item: item["code"]),
        })
    jav_vr_groups = []
    for month in sorted(vr_months):
        jav_vr_groups.append({
            "month": month,
            "items": sorted(vr_months[month], key=lambda item: item["code"]),
        })
    studios: dict[str, list[dict]] = {}
    vr_studios: dict[str, list[dict]] = {}
    west_root = settings.western_root
    vr_root = settings.vr_root
    for row in western:
        rel = row.get("path") or ""
        shelf = (row.get("shelf") or "western").strip() or "western"
        if not shelf and rel.startswith("vr/"):
            shelf = "vr"
        fs_rel = western_fs_rel(rel)
        root = vr_root if shelf == "vr" else west_root
        item = {
            "title": row.get("title") or "",
            "tpdb_id": row.get("tpdb_id") or "",
            "path": rel,
            "full_path": str(root / fs_rel) if root and fs_rel else fs_rel,
            "has_nfo": bool(row.get("has_nfo")),
            "has_poster": bool(row.get("has_poster")),
            "actors": _actors(row.get("actors")),
            "release_date": row.get("release_date") or "",
            "added_at": row.get("added_at") or 0,
        }
        bucket = vr_studios if shelf == "vr" else studios
        bucket.setdefault(row.get("studio") or "", []).append(item)
    western_groups = []
    for studio in sorted(studios):
        western_groups.append({
            "studio": studio,
            "items": sorted(studios[studio], key=lambda item: item["title"].lower()),
        })
    vr_groups = []
    for studio in sorted(vr_studios):
        vr_groups.append({
            "studio": studio,
            "items": sorted(vr_studios[studio], key=lambda item: item["title"].lower()),
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
        "jav_vr": jav_vr_groups,
        "western": western_groups,
        "vr": vr_groups,
        "suck": suck,
        "jav_root": str(settings.media_dir),
        "jav_vr_root": str(settings.jav_vr_root) if settings.jav_vr_root else "",
        "western_root": str(west_root) if west_root else "",
        "vr_root": str(vr_root) if vr_root else "",
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
    if kind == "vr":
        found = poster_file(settings.vr_root, path, "vr")
    elif kind == "western":
        found = poster_file(settings.western_root, path, "western")
        if found is None:
            found = poster_file(settings.vr_root, path, "vr")
    else:
        found = poster_file(settings.media_dir, path, "jav")
        if found is None:
            found = poster_file(settings.jav_vr_root, path, "jav")
    if found is None:
        raise HTTPException(404, "没有封面")
    return FileResponse(found, headers={"Cache-Control": "private, max-age=86400"})

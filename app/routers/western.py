from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.library import attach_western, library_info
from app.models import WesternConfirm
from app.scrape import ScrapeError
from app.sources.tpdb import (
    TpdbError,
    fetch_detail,
    fetch_facet,
    fetch_list,
    is_excluded_orientation,
    is_too_short,
    search_catalog,
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


def _fmt(fmt: str) -> str:
    value = (fmt or "").strip().lower()
    return value if value in ("vr", "flat") else "flat"


def _visible(
    items: list[dict],
    *,
    exclude_orientation: bool,
    allow_unknown_duration: bool = False,
    fmt: str = "flat",
) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        if is_too_short(item.get("duration"), allow_unknown=allow_unknown_duration):
            continue
        if exclude_orientation and is_excluded_orientation(item.get("tags") or [], item.get("title") or ""):
            continue
        vr = bool(item.get("vr"))
        if fmt == "vr" and not vr:
            continue
        if fmt == "flat" and vr:
            continue
        kept.append(item)
    return kept


async def _cached_list(
    request: Request,
    kind: str,
    page: int,
    query: str | None,
    theme: str | None = None,
    fmt: str = "flat",
    *,
    exclude_orientation: bool = False,
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
    cache_key = None if query else f"latest:tpdb:{kind}:{page}:{theme or '-'}:{fmt}"
    allow_unknown = fmt == "vr"
    if cache_key:
        cached = await db.get_metadata(cache_key, settings.latest_ttl)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            visible = _visible(
                cached["items"],
                exclude_orientation=exclude_orientation,
                allow_unknown_duration=allow_unknown
                or bool(cached.get("matched_site") or cached.get("matched_performer")),
                fmt=fmt,
            )
            return {**cached, "items": await _mark_library(request, visible), "error": None, "format": fmt}
    try:
        if query:
            payload = await search_catalog(settings, kind, query, page, theme, fmt=fmt)
        else:
            payload = await fetch_list(settings, kind, page, query, theme, fmt=fmt)
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
        "matched_site": payload.get("matched_site") or "",
        "matched_performer": payload.get("matched_performer") or "",
        "format": fmt,
    }
    if cache_key:
        await db.put_metadata(cache_key, body)
    visible = _visible(
        body["items"],
        exclude_orientation=exclude_orientation,
        allow_unknown_duration=allow_unknown
        or bool(body.get("matched_site") or body.get("matched_performer")),
        fmt=fmt,
    )
    return {**body, "items": await _mark_library(request, visible), "error": None}


async def _mark_library(request: Request, items: list[dict]) -> list[dict]:
    ids = [str(item.get("id") or "") for item in items]
    hits = await request.app.state.library.western_many(ids)
    marked = await request.app.state.db.suck_keys("western", ids)
    return attach_western(items, hits, marked)


@router.get("/api/western/latest")
async def western_latest(
    request: Request,
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
    theme: str = Query(""),
    format: str = Query("flat"),
):
    return await _cached_list(
        request, _kind(kind), page, None, _theme(theme), _fmt(format), exclude_orientation=True,
    )


@router.get("/api/western/browse")
async def western_browse(
    request: Request,
    facet: str = Query(""),
    name: str = Query(""),
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
    tag_id: str = Query(""),
    format: str = Query("flat"),
):
    kind = _kind(kind)
    facet = facet.strip().lower()
    fmt = _fmt(format)
    if facet not in ("performer", "site", "tag"):
        raise HTTPException(400, "类型无效")
    name = name.strip()
    if not name:
        raise HTTPException(400, "请填写名字")
    settings = request.app.state.settings
    if not (settings.tpdb_api_key or "").strip():
        return {
            "kind": kind,
            "page": page,
            "last_page": page,
            "items": [],
            "error": "请先在设置里填写 ThePornDB token",
        }
    try:
        payload = await fetch_facet(settings, kind, facet, name, page, tag_id=tag_id)
    except TpdbError as exc:
        return {
            "kind": kind,
            "page": page,
            "last_page": page,
            "items": [],
            "error": str(exc),
        }
    visible = _visible(
        payload["items"],
        exclude_orientation=False,
        allow_unknown_duration=fmt == "vr" or facet in ("site", "performer"),
        fmt=fmt,
    )
    return {
        "kind": kind,
        "page": payload["page"],
        "last_page": payload["last_page"],
        "items": await _mark_library(request, visible),
        "error": None,
        "format": fmt,
    }


@router.get("/api/western/search")
async def western_search(
    request: Request,
    q: str = Query(""),
    kind: str = Query("scene"),
    page: int = Query(1, ge=1, le=50),
    theme: str = Query(""),
    format: str = Query("flat"),
):
    query = q.strip()
    if not query:
        raise HTTPException(400, "请输入片名或演员")
    return await _cached_list(
        request, _kind(kind), page, query, _theme(theme), _fmt(format), exclude_orientation=True,
    )


@router.post("/api/western/pending/confirm")
async def confirm_pending(request: Request, body: WesternConfirm):
    try:
        item = await request.app.state.jobs.confirm_western_pending(
            path=(body.path or "").strip(),
            job_id=(body.job_id or "").strip(),
            tpdb_id=(body.tpdb_id or "").strip(),
            kind=(body.kind or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ScrapeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"item": item}


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
        return {"item": await _with_library(request, cached), "error": None, "cached": True}
    try:
        item = await fetch_detail(settings, kind, item_id)
    except TpdbError as exc:
        return {"item": None, "error": str(exc), "cached": False}
    await db.put_metadata(key, item)
    return {"item": await _with_library(request, item), "error": None, "cached": False}


async def _with_library(request: Request, item: dict) -> dict:
    row = dict(item)
    hits = await request.app.state.library.western_many([str(row.get("id") or "")])
    hit = hits.get(str(row.get("id") or ""))
    if hit is not None:
        hit = {**hit, "has_video": 1}
    row["library"] = library_info(hit)
    row["suck"] = await request.app.state.db.is_suck("western", str(row.get("id") or ""))
    return row

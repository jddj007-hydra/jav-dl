from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import jav_vr_maker, normalize_code
from app.follow import javbus_list_url, javbus_page_kind, javbus_page_url
from app.library import attach_library, item_code
from app.sources.javbus import (
    CACHE_VER,
    MetadataError,
    fetch_javbus_html,
    fetch_latest,
    fetch_metadata,
    is_omnibus_work,
    parse_search,
    search_works,
)
from app.sources.tpdb import is_excluded_orientation

router = APIRouter()

# One actress wall page. JavBus lists about 30 credits per page, newest first.
_ACTRESS_PAGE = 30
# Stop scanning so a star with hundreds of omnibus discs cannot walk the whole catalog.
_ACTRESS_SCAN_CAP = 20


def _fmt(raw: str) -> str:
    value = (raw or "").strip().lower()
    return value if value in ("vr", "flat") else "flat"


def _keep_format(items: list[dict], fmt: str) -> list[dict]:
    if fmt == "vr":
        return [item for item in items if jav_vr_maker(item.get("code") or "")]
    return [item for item in items if not jav_vr_maker(item.get("code") or "")]


async def _actress_cache(db, key: str, ttl: int) -> dict | None:
    getter = getattr(db, "get_metadata", None)
    if getter is None:
        return None
    payload = await getter(key, ttl)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    return payload


async def _save_actress_cache(db, key: str, payload: dict) -> None:
    setter = getattr(db, "put_metadata", None)
    if setter is None:
        return
    await setter(key, payload)


async def actress_works(settings, db, list_url: str, page: int) -> list[dict]:
    """Solo titles for one wall page, reading later star pages until this page fills."""
    key = f"actress-solo:v1:{list_url}"
    cached = await _actress_cache(db, key, settings.latest_ttl)
    items = list(cached["items"]) if cached else []
    scanned = int(cached.get("scanned") or 0) if cached else 0
    exhausted = bool(cached.get("exhausted")) if cached else False
    need = page * _ACTRESS_PAGE
    while len(items) < need and not exhausted and scanned < _ACTRESS_SCAN_CAP:
        scanned += 1
        try:
            html = await fetch_javbus_html(settings, javbus_page_url(list_url, scanned))
        except MetadataError as exc:
            if scanned == 1:
                raise
            if exc.status == 404 or items:
                exhausted = True
                break
            raise
        batch = parse_search(html, settings.javbus_base)
        if not batch:
            exhausted = True
            break
        seen = {it.get("code") for it in items}
        fresh = False
        for it in batch:
            code = it.get("code")
            if not code or code in seen:
                continue
            fresh = True
            seen.add(code)
            if is_omnibus_work(it):
                continue
            items.append(it)
        if not fresh:
            exhausted = True
            break
    await _save_actress_cache(db, key, {"items": items, "scanned": scanned, "exhausted": exhausted})
    start = (page - 1) * _ACTRESS_PAGE
    return items[start : start + _ACTRESS_PAGE]


@router.get("/api/jav/latest")
async def jav_latest(
    request: Request,
    kind: str = Query("censored"),
    page: int = Query(1, ge=1, le=50),
    format: str = Query("flat"),
):
    if kind not in ("censored", "uncensored"):
        raise HTTPException(400, "列表类型无效")
    fmt = _fmt(format)
    settings = request.app.state.settings
    db = request.app.state.db
    library = request.app.state.library
    key = f"latest:javbus:{kind}:{page}:{fmt}"
    cached = await db.get_metadata(key, settings.latest_ttl)
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        items = cached["items"]
    else:
        try:
            items = await fetch_latest(settings, kind, page, fmt)
        except MetadataError as exc:
            return {"kind": kind, "page": page, "items": [], "error": str(exc), "format": fmt}
        await db.put_metadata(key, {"kind": kind, "page": page, "items": items})
    items = [it for it in items if not is_excluded_orientation([], it.get("title") or "")]
    if fmt == "flat":
        items = _keep_format(items, "flat")
    hits = await library.get_many([it["code"] for it in items if it.get("code")])
    marked = await db.suck_keys("jav", [item_code(it.get("code") or "") for it in items])
    return {
        "kind": kind,
        "page": page,
        "items": attach_library(items, hits, marked),
        "error": None,
        "format": fmt,
    }


@router.get("/api/jav/browse")
async def jav_browse(
    request: Request,
    url: str = Query(""),
    page: int = Query(1, ge=1, le=50),
    format: str = Query("flat"),
):
    target = javbus_list_url(url.strip())
    kind = javbus_page_kind(target)
    fmt = _fmt(format)
    if kind not in ("actress", "series", "studio", "genre"):
        raise HTTPException(400, "只能打开女优、系列、厂家、发行商或类别页面")
    settings = request.app.state.settings
    db = request.app.state.db
    try:
        if kind == "actress":
            items = await actress_works(settings, db, target, page)
        else:
            html = await fetch_javbus_html(settings, javbus_page_url(target, page))
            items = parse_search(html, settings.javbus_base)
    except MetadataError as exc:
        if page > 1 and exc.status == 404:
            return {"page": page, "items": [], "error": None}
        raise HTTPException(exc.status or 400, str(exc)) from exc
    items = _keep_format(items, fmt)
    library = request.app.state.library
    hits = await library.get_many([it["code"] for it in items if it.get("code")])
    marked = await db.suck_keys("jav", [item_code(it.get("code") or "") for it in items])
    return {
        "page": page,
        "items": attach_library(items, hits, marked),
        "error": None,
        "format": fmt,
    }


@router.get("/api/search")
async def search(
    request: Request,
    q: str | None = Query(None),
    code: str | None = Query(None),
    format: str = Query("flat"),
):
    raw = (q or code or "").strip()
    if not raw:
        raise HTTPException(400, "请输入番号或关键词")
    normalized = normalize_code(raw)
    fmt = _fmt(format)
    settings = request.app.state.settings
    library = request.app.state.library
    db = request.app.state.db
    if not normalized:
        try:
            items = await search_works(settings, raw)
        except MetadataError as e:
            return {"mode": "keyword", "query": raw, "items": [], "error": str(e), "format": fmt}
        items = [it for it in items if not is_excluded_orientation([], it.get("title") or "")]
        items = _keep_format(items, fmt)
        hits = await library.get_many([it["code"] for it in items])
        marked = await db.suck_keys("jav", [item_code(it.get("code") or "") for it in items])
        return {
            "mode": "keyword",
            "query": raw,
            "items": attach_library(items, hits, marked),
            "error": None if items else "没有搜到作品",
            "format": fmt,
        }

    cache_key = f"{CACHE_VER}:{normalized}"
    lib = await library.info_for(normalized)
    suck = await db.is_suck("jav", normalized)
    cached = await db.get_metadata(cache_key, settings.metadata_ttl)
    if cached:
        return {
            "mode": "code",
            "code": normalized,
            "metadata": cached,
            "error": None,
            "cached": True,
            "library": lib,
            "suck": suck,
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
            "suck": suck,
        }
    await db.put_metadata(cache_key, meta)
    return {
        "mode": "code",
        "code": normalized,
        "metadata": meta,
        "error": None,
        "cached": False,
        "library": lib,
        "suck": suck,
    }

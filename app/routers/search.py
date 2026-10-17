from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.codes import jav_vr_maker, normalize_code
from app.follow import javbus_list_url, javbus_page_kind, javbus_page_url
from app.library import attach_library, item_code
from app.paging import JAVBUS_FULL, PAGE_SIZE, SOURCE_CAP, extend_wall, last_page_of, slice_page
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

# One actress wall page. JavBus lists about 30 credits per page, newest first;
# later star pages are read until this wall page fills.
_ACTRESS_PAGE = PAGE_SIZE
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
    key = f"actress-solo:v2:{list_url}"
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
    return slice_page(items, page, _ACTRESS_PAGE)


def _filter_jav_list(items: list[dict], fmt: str) -> list[dict]:
    kept = [item for item in items if not is_excluded_orientation([], item.get("title") or "")]
    if fmt == "flat":
        return _keep_format(kept, "flat")
    return kept


async def _jav_wall(db, key: str, ttl: int, page: int, pull) -> tuple[list[dict], int | None]:
    cached = await _actress_cache(db, key, ttl)
    state = cached if cached else {"items": [], "scanned": 0, "exhausted": False}
    items = list(state.get("items") or [])
    scanned = int(state.get("scanned") or 0)
    exhausted = bool(state.get("exhausted"))
    need = page * PAGE_SIZE
    while len(items) < need and not exhausted and scanned < SOURCE_CAP:
        scanned += 1
        batch, raw_count = await pull(scanned)
        state = extend_wall(
            {**state, "items": items, "scanned": scanned, "exhausted": exhausted},
            batch,
            src=scanned,
            raw_count=raw_count,
            full=JAVBUS_FULL,
            id_key="code",
        )
        items = state["items"]
        exhausted = state["exhausted"]
    if not exhausted and len(items) >= need and scanned < SOURCE_CAP:
        scanned += 1
        batch, raw_count = await pull(scanned)
        state = extend_wall(
            {**state, "items": items, "scanned": scanned, "exhausted": exhausted},
            batch,
            src=scanned,
            raw_count=raw_count,
            full=JAVBUS_FULL,
            id_key="code",
        )
        items = state["items"]
        exhausted = state["exhausted"]
    await _save_actress_cache(db, key, {"items": items, "scanned": scanned, "exhausted": exhausted})
    last = last_page_of(len(items)) if exhausted else None
    return slice_page(items, page), last


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
    async def pull(src: int) -> tuple[list[dict], int]:
        src_key = f"latest:javbus-src:v1:{kind}:{fmt}:{src}"
        cached = await db.get_metadata(src_key, settings.latest_ttl)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            raw = cached["items"]
        else:
            raw = await fetch_latest(settings, kind, src, fmt)
            await db.put_metadata(src_key, {"items": raw})
        return _filter_jav_list(raw, fmt), len(raw)

    try:
        items, last_page = await _jav_wall(db, f"latest:javbus:v2:{kind}:{fmt}", settings.latest_ttl, page, pull)
    except MetadataError as exc:
        return {"kind": kind, "page": page, "items": [], "error": str(exc), "format": fmt, "last_page": page}
    hits = await library.get_many([it["code"] for it in items if it.get("code")])
    marked = await db.suck_keys("jav", [item_code(it.get("code") or "") for it in items])
    return {
        "kind": kind,
        "page": page,
        "last_page": last_page,
        "page_size": PAGE_SIZE,
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
            last_page = None
        else:
            async def pull(src: int) -> tuple[list[dict], int]:
                try:
                    html = await fetch_javbus_html(settings, javbus_page_url(target, src))
                except MetadataError as exc:
                    if src > 1 and exc.status == 404:
                        return [], 0
                    raise
                raw = parse_search(html, settings.javbus_base)
                return _keep_format(raw, fmt), len(raw)

            items, last_page = await _jav_wall(
                db, f"browse:javbus:v2:{target}:{fmt}", settings.latest_ttl, page, pull,
            )
    except MetadataError as exc:
        if page > 1 and exc.status == 404:
            return {"page": page, "items": [], "error": None, "last_page": page}
        raise HTTPException(exc.status or 400, str(exc)) from exc
    library = request.app.state.library
    hits = await library.get_many([it["code"] for it in items if it.get("code")])
    marked = await db.suck_keys("jav", [item_code(it.get("code") or "") for it in items])
    return {
        "page": page,
        "last_page": last_page,
        "page_size": PAGE_SIZE,
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

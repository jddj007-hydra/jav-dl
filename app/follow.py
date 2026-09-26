"""Watch JavBus and ThePornDB for new works. Remind by default; download only when asked."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

from app.codes import normalize_code
from app.config import Settings
from app.notify import send_notice
from app.ranking import sort_resources
from app.slug import western_slug
from app.sources.clm import MagnetSearchError, search_magnets
from app.sources.javbus import (
    MetadataError,
    fetch_javbus_html,
    javbus_page_kind,
    parse_search,
    parse_star_links,
)
from app.sources.tpdb import TpdbError, catalog_id, scenes_for_performer, scenes_for_site
from app.western_archive import read_sidecar, write_sidecar

log = logging.getLogger("app.follow")

FOLLOW_EVERY = 3 * 60 * 60
FOLLOW_LIMIT = 30
FOLLOW_PAGE_CAP = 5
FOLLOW_RETRY_FOR = 14 * 24 * 60 * 60
KINDS = ("actress", "series", "studio", "western_performer", "western_studio")
KIND_LABEL = {
    "actress": "女优",
    "series": "系列",
    "studio": "片商",
    "western_performer": "欧美演员",
    "western_studio": "欧美片商",
}


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def magnet_matches(item: dict, sub: dict) -> bool:
    tags = set(item.get("tags") or [])
    wants = []
    if sub.get("want_uc"):
        wants.append("UC")
    if sub.get("want_c"):
        wants.append("C")
    if wants and not any(tag in tags for tag in wants):
        return False
    max_gb = int(sub.get("max_gb") or 0)
    if max_gb > 0:
        size = int(item.get("size_bytes") or 0)
        if size and size > max_gb * 1000**3:
            return False
    return True


async def resolve_target(settings: Settings, kind: str, name: str, target: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise ValueError("关注类型无效")
    name = (name or "").strip()
    target = (target or "").strip()
    if target:
        return _resolve_url(kind, name, target)
    if not name:
        raise ValueError("请填写名字或页面链接")
    if kind == "actress":
        url = f"{settings.javbus_base.rstrip('/')}/searchstar/{quote(name)}"
        html = await fetch_javbus_html(settings, url)
        links = parse_star_links(html, settings.javbus_base)
        if not links:
            raise ValueError("没有找到这个女优")
        picked = next((item for item in links if item["name"].casefold() == name.casefold()), None)
        if picked is None:
            raise ValueError("没有找到这个女优")
        return picked["name"], picked["url"]
    if kind == "western_performer":
        return name, f"tpdb:performer:{name}"
    if kind == "western_studio":
        return name, f"tpdb:site:{name}"
    raise ValueError("系列和片商请粘贴 JavBus 页面链接")


def _resolve_url(kind: str, name: str, target: str) -> tuple[str, str]:
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("链接无效")
    page_kind = javbus_page_kind(target)
    if page_kind:
        if page_kind != kind:
            raise ValueError(f"这个链接不是{KIND_LABEL.get(kind, '该类型')}")
        clean = javbus_list_url(target)
        label = name or clean.rsplit("/", 1)[-1]
        return label, clean
    raise ValueError("请粘贴 JavBus 的女优、系列或片商页面")


_JAVBUS_LISTS = ("star", "series", "studio", "label")


def javbus_list_url(target: str) -> str:
    """The list URL without a page suffix. Ids can be all digits, so only the segment after the id is a page."""
    clean = (target or "").split("?")[0].split("#")[0].rstrip("/")
    parsed = urlparse(clean)
    parts = [part for part in parsed.path.split("/") if part]
    for i, part in enumerate(parts):
        if part in _JAVBUS_LISTS and i + 1 < len(parts):
            return parsed._replace(path="/" + "/".join(parts[: i + 2])).geturl()
    return clean


def javbus_page_url(target: str, page: int) -> str:
    """JavBus star, series, and studio pages put the page number on the path."""
    base = javbus_list_url(target)
    if page <= 1:
        return base
    return f"{base}/{int(page)}"


async def _walk_pages(fetch_page, known: set[str], pending: set[str] | None = None) -> list[dict]:
    """Read newer pages until one is already recorded, empty, or repeated.

    A recorded page only ends the walk once every pending retry code has been reached.
    """
    waiting = set(pending or ())
    works: list[dict] = []
    collected: set[str] = set()
    for page in range(1, FOLLOW_PAGE_CAP + 1):
        batch = await fetch_page(page)
        page_codes = [str(item.get("code") or "") for item in batch if item.get("code")]
        if not page_codes or all(code in collected for code in page_codes):
            break
        for item in batch:
            code = item.get("code")
            if not code or code in collected:
                continue
            collected.add(code)
            works.append(item)
        if known and all(code in known for code in page_codes) and waiting <= collected:
            break
    return works


def _jav_work(item: dict) -> dict | None:
    code = normalize_code(item.get("code") or "")
    if not code:
        return None
    return {"code": code, "title": item.get("title") or code, "western": None}


def _western_work(scene: dict, sub: dict) -> dict | None:
    scene_id = str(scene.get("id") or "").strip()
    if not scene_id:
        return None
    return {
        "code": scene_id,
        "title": scene.get("title") or scene_id,
        "western": {
            "id": scene_id,
            "kind": scene.get("kind") or "scene",
            "site": scene.get("site") or sub["name"],
            "title": scene.get("title") or scene_id,
            "date": scene.get("date") or "",
            "performers": scene.get("performers") or [],
        },
    }


async def _jav_page(settings: Settings, target: str, page: int) -> list[dict]:
    try:
        html = await fetch_javbus_html(settings, javbus_page_url(target, page))
    except MetadataError as exc:
        if page > 1 and exc.status == 404:
            return []
        raise
    works = []
    for item in parse_search(html, settings.javbus_base):
        work = _jav_work(item)
        if work:
            works.append(work)
    return works


async def _western_page(settings: Settings, sub: dict, ident: list[str], page: int) -> list[dict]:
    kind = sub["kind"]
    if not ident:
        ident.append(await catalog_id(settings, kind, sub["name"]))
    if kind == "western_performer":
        scenes = await scenes_for_performer(settings, sub["name"], page, performer_id=ident[0])
    else:
        scenes = await scenes_for_site(settings, sub["name"], page, site_id=ident[0])
    works = []
    for scene in scenes:
        work = _western_work(scene, sub)
        if work:
            works.append(work)
    return works


async def list_works(
    settings: Settings,
    sub: dict,
    known: set[str] | None = None,
    pending: set[str] | None = None,
) -> list[dict]:
    seen = known or set()
    kind = sub["kind"]
    if kind in ("actress", "series", "studio"):
        target = sub["target"]

        async def fetch_page(page: int) -> list[dict]:
            return await _jav_page(settings, target, page)

        return await _walk_pages(fetch_page, seen, pending)
    ident: list[str] = []

    async def fetch_western(page: int) -> list[dict]:
        return await _western_page(settings, sub, ident, page)

    return await _walk_pages(fetch_western, seen, pending)


async def _owned(db, library, work: dict) -> bool:
    code = work["code"]
    if await db.is_suck("western" if work.get("western") else "jav", code):
        return True
    if work.get("western"):
        if await db.western_has_id(code):
            return True
        for job in await db.list_jobs():
            if job.get("status") == "cancelled":
                continue
            info = read_sidecar(Path(job.get("dest") or ""))
            if info and str(info.get("tpdb_id") or "") == code:
                return True
        return False
    if library is not None:
        row = await library.get(code)
        if row and row.get("has_video"):
            return True
    return await db.code_in_queue(code)


async def _download(manager, sub: dict, work: dict) -> tuple[str, str]:
    """Return hit status and detail. Uses the current magnet sort."""
    if work.get("western"):
        from app.western_magnets import collect_western_magnets

        info = work["western"]
        items, _match, error = await collect_western_magnets(
            manager.settings,
            info.get("site") or "",
            info.get("title") or "",
            info.get("performers") or [],
            info.get("date") or "",
        )
        if error and not items:
            return "no_magnet", error
    else:
        try:
            magnets = await search_magnets(manager.settings, work["code"])
        except MagnetSearchError as exc:
            return "no_magnet", str(exc)
        items = sort_resources(magnets, work["code"])
    picked = next((item for item in items if magnet_matches(item, sub)), None)
    if not picked:
        return "no_magnet", "没有符合规则的磁链"
    info_hash = str(picked.get("info_hash") or "")
    title = picked.get("title") or work["title"]
    if work.get("western"):
        info = work["western"]
        slug = western_slug(info.get("site") or "", info.get("date") or "", info.get("title") or title, info.get("id") or "")
        job = await _enqueue(manager, slug, info_hash, title, dest_rel=f"western/{slug}")
        write_sidecar(Path(job["dest"]), {
            "kind": "western",
            "tpdb_id": info.get("id") or "",
            "tpdb_kind": info.get("kind") or "scene",
            "site": info.get("site") or "",
            "title": info.get("title") or title,
            "date": info.get("date") or "",
            "performers": info.get("performers") or [],
        })
    else:
        await _enqueue(manager, work["code"], info_hash, title)
    return "queued", title


async def _enqueue(manager, code: str, info_hash: str, title: str, dest_rel: str | None = None) -> dict:
    """Batch and follow downloads drop ads without asking."""
    filtered = getattr(manager, "enqueue_filtered", None)
    if filtered is not None:
        return await filtered(code, info_hash, title, dest_rel=dest_rel)
    return await manager.enqueue(code, info_hash, title, dest_rel=dest_rel)


def _sub_lock(manager, sub_id: str) -> asyncio.Lock:
    locks = getattr(manager, "_follow_locks", None)
    if locks is None:
        locks = {}
        manager._follow_locks = locks
    lock = locks.get(sub_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[sub_id] = lock
    return lock


async def _store_hit(db, sub: dict, work: dict, status: str, detail: str) -> bool:
    """Record a hit. The same status stays quiet so a later retry does not notify again."""
    existing = await db.find_hit(sub["id"], work["code"])
    if existing:
        if existing.get("status") == status:
            return False
        await db.update_hit(existing["id"], status=status, detail=detail)
        return True
    await db.add_hit({
        "id": _new_id(),
        "sub_id": sub["id"],
        "code": work["code"],
        "title": work["title"],
        "status": status,
        "detail": detail,
        "created_at": time.time(),
    })
    return True


async def _notify_hit(settings: Settings, sub: dict, work: dict, status: str, detail: str) -> None:
    label = f"{work['code']} {work['title']}".strip()
    source = sub.get("name") or ""
    if status == "queued":
        await send_notice(settings, "追更已入队", f"{label}\n来自 {source}")
    elif status == "no_magnet":
        await send_notice(settings, "追更没有符合规则的磁链", f"{label}\n{detail}\n来自 {source}")
    else:
        await send_notice(settings, "追更新作", f"{label}\n来自 {source}")


async def check_sub(manager, sub: dict) -> dict:
    async with _sub_lock(manager, sub["id"]):
        return await _check_sub(manager, sub)


async def _retry_codes(db, sub_id: str, seen: set[str]) -> set[str]:
    """Codes still waiting for a magnet. Past FOLLOW_RETRY_FOR they are given up on."""
    cutoff = time.time() - FOLLOW_RETRY_FOR
    pending: set[str] = set()
    for hit in await db.pending_hits(sub_id):
        code = hit["code"]
        if float(hit.get("created_at") or 0) < cutoff:
            await db.mark_seen(sub_id, code)
            seen.add(code)
        else:
            pending.add(code)
    return pending


async def _check_sub(manager, sub: dict) -> dict:
    first = not sub.get("last_check")
    seen = await manager.db.seen_codes(sub["id"])
    pending = await _retry_codes(manager.db, sub["id"], seen)
    try:
        works = await list_works(manager.settings, sub, seen, pending)
    except (MetadataError, TpdbError, ValueError) as exc:
        await manager.db.mark_subscription_checked(sub["id"], error=str(exc), stamp=not first)
        return {"first": first, "added": 0, "error": str(exc)}
    library = manager.library
    added = 0
    for work in works:
        if work["code"] in seen:
            continue
        if first or await _owned(manager.db, library, work):
            await manager.db.mark_seen(sub["id"], work["code"])
            seen.add(work["code"])
            continue
        status, detail = ("new", "")
        if sub.get("auto"):
            try:
                status, detail = await _download(manager, sub, work)
            except Exception as exc:
                log.warning("追更入队失败 %s", work["code"], exc_info=True)
                status, detail = "no_magnet", str(exc)
        # A missing magnet can show up later. Remember it only after it is queued or only needs a reminder.
        if status != "no_magnet":
            await manager.db.mark_seen(sub["id"], work["code"])
            seen.add(work["code"])
        if not await _store_hit(manager.db, sub, work, status, detail):
            continue
        added += 1
        await _notify_hit(manager.settings, sub, work, status, detail)
    # An empty first listing (a blocked or changed page) has recorded nothing yet.
    await manager.db.mark_subscription_checked(sub["id"], error=None, stamp=not first or bool(works))
    return {"first": first, "added": added, "error": None, "known": len(works)}


async def check_due(manager) -> None:
    now = time.time()
    subs = await manager.db.list_subscriptions()
    if not subs:
        return
    if now - getattr(manager, "_last_follow", 0.0) < FOLLOW_EVERY:
        return
    manager._last_follow = now
    for sub in subs:
        try:
            await check_sub(manager, sub)
        except Exception:
            log.warning("追更检查失败 %s", sub.get("id"), exc_info=True)


def new_subscription(kind: str, name: str, target: str, *, auto: bool, want_uc: bool, want_c: bool, max_gb: int) -> dict:
    now = time.time()
    return {
        "id": _new_id(),
        "kind": kind,
        "name": name,
        "target": target,
        "auto": 1 if auto else 0,
        "want_uc": 1 if want_uc else 0,
        "want_c": 1 if want_c else 0,
        "max_gb": max(0, int(max_gb)),
        "created_at": now,
        "last_check": None,
        "last_error": None,
    }

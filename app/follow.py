"""Watch JavBus and ThePornDB for new works. Remind by default; download only when asked."""

from __future__ import annotations

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
from app.sources.tpdb import TpdbError, scenes_for_performer, scenes_for_site
from app.western_archive import read_sidecar, write_sidecar

log = logging.getLogger("app.follow")

FOLLOW_EVERY = 3 * 60 * 60
FOLLOW_LIMIT = 30
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
        picked = next((item for item in links if item["name"].casefold() == name.casefold()), links[0])
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
        label = name or parsed.path.rstrip("/").split("/")[-1]
        return label, target.split("?")[0].rstrip("/")
    raise ValueError("请粘贴 JavBus 的女优、系列或片商页面")


async def list_works(settings: Settings, sub: dict) -> list[dict]:
    kind = sub["kind"]
    target = sub["target"]
    if kind in ("actress", "series", "studio"):
        html = await fetch_javbus_html(settings, target)
        works = []
        for item in parse_search(html, settings.javbus_base):
            code = normalize_code(item.get("code") or "")
            if not code:
                continue
            works.append({"code": code, "title": item.get("title") or code, "western": None})
        return works
    if kind == "western_performer":
        scenes = await scenes_for_performer(settings, sub["name"])
    else:
        scenes = await scenes_for_site(settings, sub["name"])
    works = []
    for scene in scenes:
        scene_id = str(scene.get("id") or "").strip()
        if not scene_id:
            continue
        works.append({
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
        })
    return works


async def _owned(db, library, work: dict) -> bool:
    code = work["code"]
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
        job = await manager.enqueue(slug, info_hash, title, dest_rel=f"western/{slug}")
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
        await manager.enqueue(work["code"], info_hash, title)
    return "queued", title


async def check_sub(manager, sub: dict) -> dict:
    first = not sub.get("last_check")
    try:
        works = await list_works(manager.settings, sub)
    except (MetadataError, TpdbError, ValueError) as exc:
        await manager.db.mark_subscription_checked(sub["id"], error=str(exc))
        return {"first": first, "added": 0, "error": str(exc)}
    seen = await manager.db.seen_codes(sub["id"])
    library = manager.library
    added = 0
    for work in works:
        if work["code"] in seen:
            continue
        await manager.db.mark_seen(sub["id"], work["code"])
        if first:
            continue
        if await _owned(manager.db, library, work):
            continue
        added += 1
        status, detail = ("new", "")
        if sub.get("auto"):
            try:
                status, detail = await _download(manager, sub, work)
            except Exception as exc:
                log.warning("追更入队失败 %s", work["code"], exc_info=True)
                status, detail = "no_magnet", str(exc)
        await manager.db.add_hit({
            "id": _new_id(),
            "sub_id": sub["id"],
            "code": work["code"],
            "title": work["title"],
            "status": status,
            "detail": detail,
            "created_at": time.time(),
        })
        label = f"{work['code']} {work['title']}".strip()
        source = sub.get("name") or ""
        if status == "queued":
            await send_notice(manager.settings, "追更已入队", f"{label}\n来自 {source}")
        elif status == "no_magnet":
            await send_notice(manager.settings, "追更没有符合规则的磁链", f"{label}\n{detail}\n来自 {source}")
        else:
            await send_notice(manager.settings, "追更新作", f"{label}\n来自 {source}")
    await manager.db.mark_subscription_checked(sub["id"], error=None)
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

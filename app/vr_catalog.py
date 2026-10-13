"""VR files that already have a magnet on 磁力猫.

The list is a feed. A refresh reads each studio from the newest page and
stops when a stored hash shows up. A studio with nothing stored yet keeps
only the newest few pages; later refreshes add files that appeared since.

Rows that match ThePornDB keep the cover, title, performers, and release
date. Each page fills a few unmatched names so the list can come back first.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.db import Database
from app.httputil import site_client
from app.ranking import is_pack
from app.sources.tpdb import TpdbError, fetch_by_filename
from app.sources.clm import (
    MagnetSearchError,
    _b64_word,
    _origin,
    _search_origins,
    fetch_search_page,
    parse_search_html,
    unwrap_search_html,
)
from app.trackers import magnet_for

# 磁力猫一页 12 条。没订过的片商只留最近几页；订过之后一直翻到遇见旧文件。
CLM_PAGE = 12
FEED_PAGES = 5
PAGE_CAP = 120
LIST_PAGE = 40
TICK_PAGES = 2
# 一页先补几部，剩下的等下一次打开再查，避免整页卡住。
MATCH_BATCH = 3
# 刚核对过的话，这段时间里打开页面直接用已收下的列表。
REFRESH_AFTER = 10 * 60
# 小于这个的多半是预告或垃圾行。未知体积留着。
MIN_BYTES = 200 * 1000**2
_TIP_RE = re.compile(r"相关结果(?:<[^>]+>)?\s*(\d+)\s*(?:<[^>]+>)?\s*个")
_VR_RE = re.compile(
    r"vr180|vr360|_180_lr|180x180|fisheye|oculus|sidebyside|gearvr|psvr|"
    r"(?<![a-z0-9])(?:8k|7k|6k|5k|4096p|3840p|3630p|4320p)(?![a-z0-9])|"
    r"(?<![a-z0-9])vr(?![a-z0-9])",
    re.IGNORECASE,
)
_crawl_lock = asyncio.Lock()


@dataclass(frozen=True)
class Probe:
    query: str
    label: str
    # 片商名会搜出平面片时，文件名里还要有 VR 标记。
    require_vr: bool


# 这些词在磁力猫上能稳定搜到欧美 VR。裸的 SLR 会混进番号和汽车，不收。
PROBES: tuple[Probe, ...] = (
    Probe("VirtualTaboo", "VirtualTaboo", False),
    Probe("CzechVR", "CzechVR", False),
    Probe("WankzVR", "WankzVR", False),
    Probe("VRBangers", "VRBangers", False),
    Probe("BadoinkVR", "BadoinkVR", False),
    Probe("VRHush", "VRHush", False),
    Probe("RealJamVR", "RealJamVR", False),
    Probe("SexBabesVR", "SexBabesVR", False),
    Probe("VirtualRealPorn", "VirtualRealPorn", False),
    Probe("NaughtyAmericaVR", "NaughtyAmericaVR", False),
    Probe("VRConk", "VRConk", False),
    Probe("Dezyred", "Dezyred", False),
    Probe("SLROriginals", "SLR Originals", False),
    Probe("SexLikeReal", "SexLikeReal", True),
    Probe("POVR", "POVR", True),
    Probe("DormRoom", "DormRoom", True),
)


def keep_file(title: str, size_bytes: int | None, require_vr: bool) -> bool:
    """True when a 磁力猫 row is a VR scene we can list."""
    name = title or ""
    if is_pack(name, None):
        return False
    if size_bytes is not None and 0 < size_bytes < MIN_BYTES:
        return False
    if require_vr and not _VR_RE.search(name):
        return False
    return True


def studio_finished(page: int, raw_count: int, total: int | None) -> bool:
    """A short page, a known total, or the page cap ends one studio."""
    if raw_count < CLM_PAGE:
        return True
    if total is not None and page * CLM_PAGE >= total:
        return True
    return page >= PAGE_CAP


def feed_caught_up(
    page: int,
    raw_count: int,
    total: int | None,
    *,
    overlapped: bool,
    subscribed: bool,
) -> bool:
    """Stop on a known file. A first look also stops at the feed window."""
    if overlapped or studio_finished(page, raw_count, total):
        return True
    return not subscribed and page >= FEED_PAGES


def _tip_total(*chunks: str) -> int | None:
    for chunk in chunks:
        match = _TIP_RE.search(chunk or "")
        if match:
            return int(match.group(1))
    return None


async def fetch_magnet_page(
    settings: Settings,
    query: str,
    page: int,
) -> tuple[list[dict], int | None, str | None]:
    """One 磁力猫 time-sorted page. Returns rows, the reported total, and an error."""
    word = _b64_word(query)
    origins = _search_origins(settings)
    home = _origin(settings.clm_home)
    if not origins and home:
        origins = [home]
    if not origins:
        return [], None, "没有配置磁力猫搜索地址"
    last_error = "磁力猫没有结果"
    async with site_client(settings) as client:
        if home:
            try:
                await client.get(home + "/")
            except httpx.HTTPError:
                pass
        for base in origins:
            url = f"{base}/search?word={word}&sort=time"
            if page > 1:
                url += f"&p={page}"
            try:
                raw = await fetch_search_page(client, url)
            except MagnetSearchError as exc:
                last_error = str(exc)
                continue
            except httpx.HTTPError as exc:
                last_error = f"磁力猫请求失败: {exc}"
                continue
            html, _redirect = unwrap_search_html(raw)
            total = _tip_total(html, raw)
            return parse_search_html(html, query), total, None
    return [], None, last_error


def _fresh(states: dict[str, dict], probes: tuple[Probe, ...], now: float) -> bool:
    stamps: list[float] = []
    for probe in probes:
        state = states.get(probe.query)
        if not state or not int(state.get("done") or 0):
            return False
        stamps.append(float(state.get("updated_at") or 0))
    if not stamps:
        return False
    return now - max(stamps) < REFRESH_AFTER


async def _open_round(db: Database, probes: tuple[Probe, ...], *, force: bool) -> bool:
    """True when this tick should fetch. A finished round waits out the interval."""
    states = await db.vr_crawl_all()
    if _fresh(states, probes, time.time()) and not force:
        return False
    pending = [
        probe
        for probe in probes
        if not int((states.get(probe.query) or {}).get("done") or 0)
    ]
    if pending:
        return True
    for probe in probes:
        total = int((states.get(probe.query) or {}).get("total") or 0)
        await db.vr_crawl_put(probe.query, 0, total, False, "", 0)
    return True


def _kept_row(row: dict, probe: Probe) -> dict | None:
    info_hash = row.get("info_hash") or ""
    if not info_hash:
        return None
    size_bytes = row.get("size_bytes")
    if size_bytes is None:
        size_bytes = 0
    if not keep_file(row.get("title") or "", int(size_bytes) or None, probe.require_vr):
        return None
    return {
        "info_hash": info_hash,
        "title": row.get("title") or "",
        "size": row.get("size") or "",
        "size_bytes": int(size_bytes or 0),
        "heat": int(row.get("heat") or 0),
        "created": row.get("date") or "",
        "studio": probe.label,
    }


async def _advance(
    db: Database,
    probe: Probe,
    fetch_page,
) -> tuple[str | None, int]:
    """Fetch the next newest page. The error is empty when the page was read."""
    state = await db.vr_crawl_get(probe.query)
    if state.get("done"):
        return None, 0
    stored_page = int(state.get("page") or 0)
    anchor = 1 if int(state.get("anchor") or 0) else 0
    # 旧的整库游标停在很后面。丢掉它，改从最新一页重新核对。
    if stored_page >= FEED_PAGES and not anchor:
        stored_page = 0
    page = stored_page + 1
    if page == 1:
        anchor = 1 if await db.vr_studio_has_magnet(probe.label) else 0
    try:
        rows, total, error = await fetch_page(probe.query, page)
    except Exception as exc:
        error = f"磁力猫请求失败: {exc}"
        rows, total = [], None
    if error:
        await db.vr_crawl_put(
            probe.query,
            stored_page,
            int(state.get("total") or 0),
            True,
            error,
            anchor,
        )
        return error, 0
    hashes = [row.get("info_hash") or "" for row in rows]
    known = await db.vr_magnet_known(hashes)
    kept = []
    added = 0
    for row in rows:
        item = _kept_row(row, probe)
        if not item:
            continue
        kept.append(item)
        if item["info_hash"] not in known:
            added += 1
    await db.upsert_vr_magnets(kept)
    reported = total if total is not None else int(state.get("total") or 0)
    done = feed_caught_up(
        page,
        len(rows),
        total,
        overlapped=bool(known),
        subscribed=bool(anchor),
    )
    await db.vr_crawl_put(probe.query, page, reported, done, "", anchor)
    return None, added


async def crawl_tick(
    settings: Settings,
    db: Database,
    *,
    pages: int = TICK_PAGES,
    fetch_page=None,
    probes: tuple[Probe, ...] | None = None,
    force: bool = False,
) -> int:
    """Pull the newest pages until a known file, or skip when the feed is fresh."""
    probes = probes if probes is not None else PROBES
    fetch = fetch_page or (lambda query, page: fetch_magnet_page(settings, query, page))
    added = 0
    async with _crawl_lock:
        if not await _open_round(db, probes, force=force):
            return 0
        budget = max(1, pages)
        for probe in probes:
            if budget <= 0:
                return added
            while budget > 0:
                state = await db.vr_crawl_get(probe.query)
                if state.get("done"):
                    break
                error, count = await _advance(db, probe, fetch)
                added += count
                budget -= 1
                if error:
                    break
    return added


def _meta_performers(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(name) for name in data if str(name).strip()]


def _ready_item(row: dict) -> dict:
    state = row.get("match_state") or ""
    info_hash = row.get("info_hash") or ""
    title = row.get("title") or ""
    duration = int(row.get("meta_duration") or 0)
    return {
        "info_hash": info_hash,
        "title": title,
        "size": row.get("size") or "",
        "date": row.get("created") or "",
        "studio": row.get("studio") or "",
        "heat": int(row.get("heat") or 0),
        "magnet": magnet_for(info_hash, title) if info_hash else "",
        "match": state if state in ("hit", "miss") else "pending",
        "id": row.get("tpdb_id") or "",
        "kind": row.get("tpdb_kind") or "scene",
        "work_title": row.get("meta_title") or "",
        "site": row.get("meta_site") or "",
        "release_date": row.get("meta_date") or "",
        "cover": row.get("meta_cover") or "",
        "performers": _meta_performers(row.get("meta_performers")),
        "duration": duration or None,
    }


async def match_vr_title(settings: Settings, title: str) -> tuple[str, dict | None]:
    """A confident ThePornDB row, or miss when the name is not in the catalog.

    A token, network, or HTTP failure stays pending so the next open can retry.
    """
    try:
        item = await fetch_by_filename(settings, title)
    except TpdbError as exc:
        text = str(exc)
        if text.startswith("没有匹配") or text.startswith("没有可解析"):
            return "miss", None
        raise
    if not item or not item.get("id"):
        return "miss", None
    return "hit", item


def _unmatched(rows: list[dict]) -> list[dict]:
    return [row for row in rows if (row.get("match_state") or "") not in ("hit", "miss")]


async def _fill_metadata(
    settings: Settings,
    db: Database,
    rows: list[dict],
    query: str,
    page: int,
    match_title,
) -> tuple[list[dict], bool, str]:
    """Fill a few names on this page. True means some rows are still pending."""
    pending = _unmatched(rows)
    if not pending or not (settings.tpdb_api_key or "").strip():
        return rows, False, ""
    done = 0
    note = ""
    for row in pending[:MATCH_BATCH]:
        try:
            state, item = await match_title(settings, row.get("title") or "")
        except TpdbError as exc:
            note = f"元数据暂时没查到：{exc}"
            break
        await db.save_vr_match(row.get("info_hash") or "", state, item if state == "hit" else None)
        done += 1
    if done:
        _total, rows = await db.list_vr_magnets(query, LIST_PAGE, (page - 1) * LIST_PAGE)
    return rows, bool(_unmatched(rows)), note


def _progress(states: dict[str, dict], probes: tuple[Probe, ...]) -> tuple[bool, str]:
    for probe in probes:
        state = states.get(probe.query) or {}
        if state.get("done"):
            continue
        page = int(state.get("page") or 0) + 1
        return True, f"正在核对 {probe.label} 的新文件，第 {page} 页"
    failed = [
        probe.label
        for probe in probes
        if (states.get(probe.query) or {}).get("error")
    ]
    if failed:
        return False, "没核对完：" + "、".join(failed)
    return False, ""


async def list_downloadable(
    settings: Settings,
    db: Database,
    page: int,
    query: str = "",
    *,
    probes: tuple[Probe, ...] | None = None,
    fetch_page=None,
    refresh: bool = False,
    match_title=None,
) -> dict:
    probes = probes if probes is not None else PROBES
    added = await crawl_tick(settings, db, fetch_page=fetch_page, probes=probes, force=refresh)
    page = max(1, page)
    total, rows = await db.list_vr_magnets(query, LIST_PAGE, (page - 1) * LIST_PAGE)
    last_page = max(1, math.ceil(total / LIST_PAGE)) if total else 1
    if page > last_page:
        page = last_page
        total, rows = await db.list_vr_magnets(query, LIST_PAGE, (page - 1) * LIST_PAGE)
    rows, matching, meta_error = await _fill_metadata(
        settings,
        db,
        rows,
        query,
        page,
        match_title or match_vr_title,
    )
    states = await db.vr_crawl_all()
    scanning, progress = _progress(states, probes)
    return {
        "items": [_ready_item(row) for row in rows],
        "page": page,
        "last_page": last_page,
        "total": total,
        "scanning": scanning,
        "progress": progress,
        "added": added,
        "matching": matching,
        "meta_error": meta_error,
    }

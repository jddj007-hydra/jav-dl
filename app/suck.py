"""A suck mark blocks another download after the file is gone."""

from __future__ import annotations

import asyncio

from app.codes import normalize_code
from app.config import Settings
from app.db import Database
from app.library import remove_archived


def parse_suck(kind: str, key: str) -> tuple[str, str]:
    kind = (kind or "").strip().lower()
    if kind not in ("jav", "western"):
        raise ValueError("类型无效")
    raw = (key or "").strip()
    if kind == "jav":
        code = normalize_code(raw)
        if not code:
            raise ValueError("番号格式无效")
        return kind, code
    if not raw or any(ch in raw for ch in "/\\") or len(raw) > 80:
        raise ValueError("缺少作品 id")
    return kind, raw


async def _known(db: Database, kind: str, key: str) -> dict | None:
    if kind == "jav":
        return await db.get_library(key)
    return (await db.western_by_ids([key])).get(key)


async def mark_work(db: Database, settings: Settings, *, kind: str, key: str, title: str, remove: bool) -> dict:
    kind, key = parse_suck(kind, key)
    row = await _known(db, kind, key)
    label = (title or "").strip() or (row or {}).get("title") or key
    label = label[:300]
    await db.mark_suck(kind, key, label)
    removed = False
    if remove and row and row.get("path"):
        if kind == "jav":
            removed = await asyncio.to_thread(remove_archived, settings.media_dir, row["path"], "jav")
            if not removed and settings.jav_vr_root is not None:
                removed = await asyncio.to_thread(remove_archived, settings.jav_vr_root, row["path"], "jav")
        else:
            root = (
                settings.vr_root if (row.get("shelf") or "") == "vr" or str(row.get("path") or "").startswith("vr/")
                else settings.western_root
            )
            removed = await asyncio.to_thread(
                remove_archived,
                root,
                row["path"],
                "vr" if root == settings.vr_root else kind,
            )
        if kind == "jav":
            await db.delete_library(key)
        else:
            await db.delete_western(row["path"])
    saved = await db.get_suck(kind, key) or {"kind": kind, "key": key, "title": label}
    return {**saved, "removed": removed}

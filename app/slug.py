from __future__ import annotations

import re

from app.codes import normalize_code

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def western_slug(site: str, date: str, title: str, tpdb_id: str = "") -> str:
    """Directory name for a western download. Never a JAV code."""
    raw = " ".join(part for part in (site or "", date or "", title or "") if part).lower()
    slug = _NON_SLUG.sub("-", raw).strip("-")[:60].strip("-")
    if not slug:
        slug = _NON_SLUG.sub("-", (tpdb_id or "item").lower()).strip("-")[:40] or "item"
    if normalize_code(slug):
        slug = f"x-{slug}"
    if normalize_code(slug):
        slug = f"item-{slug}"[:80]
    return slug

"""Wall page size. 24 fills 4×6 (疏), 6×4 (密), and 2×12 (narrow)."""

from __future__ import annotations

import math

PAGE_SIZE = 24
SOURCE_CAP = 50
# JavBus list pages are about 30. A shorter page is treated as the last source page.
JAVBUS_FULL = 20


def slice_page(items: list, page: int, size: int = PAGE_SIZE) -> list:
    page = max(1, int(page))
    start = (page - 1) * size
    return items[start : start + size]


def last_page_of(count: int, size: int = PAGE_SIZE) -> int:
    if count <= 0:
        return 1
    return max(1, math.ceil(count / size))


def extend_wall(
    state: dict | None,
    batch: list,
    *,
    src: int,
    raw_count: int,
    source_last: int | None = None,
    full: int | None = None,
    id_key: str = "id",
) -> dict:
    """Append unique rows. A short or repeated source page ends the walk."""
    prev = state if isinstance(state, dict) else {}
    items = list(prev.get("items") or [])
    seen = {str(item.get(id_key) or item.get("code") or "") for item in items}
    seen.discard("")
    fresh = []
    for item in batch or []:
        key = str(item.get(id_key) or item.get("code") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        fresh.append(item)
    exhausted = True
    if fresh:
        items.extend(fresh)
        exhausted = False
        if raw_count <= 0:
            exhausted = True
        elif full is not None and raw_count < full:
            exhausted = True
        elif source_last is not None and src >= int(source_last):
            exhausted = True
    extra = {
        key: value
        for key, value in prev.items()
        if key not in ("items", "scanned", "exhausted")
    }
    return {**extra, "items": items, "scanned": src, "exhausted": exhausted}

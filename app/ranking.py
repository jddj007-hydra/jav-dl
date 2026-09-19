from __future__ import annotations

import re

from app.codes import title_mentions_code
from app.textutil import parse_size

# Lower is better.
TIER_UC = 0
TIER_U = 1
TIER_C = 2
TIER_NONE = 3
TIER_PACK = 9

_PACK_RE = re.compile(
    r"(\d{3}\s*[-~～到至]\s*\d{3})|(合集)|(打包)|(全集)",
    re.IGNORECASE,
)
# 15 GB — single-title releases are almost never this large.
PACK_SIZE = 15 * 1000**3


def detect_tags(title: str) -> list[str]:
    t = title.upper()
    tags: list[str] = []
    if re.search(r"(?<![A-Z])UC(?![A-Z])", t) or "无码破解" in title or "無碼破解" in title:
        tags.append("UC")
    if (
        "无码" in title
        or "無碼" in title
        or "破解" in title
        or "REDUCING MOSAIC" in t
        or "UNCENSORED" in t
        or re.search(r"(?<![A-Z])U(?![A-Z])", t)
    ):
        if "UC" not in tags:
            tags.append("U")
    if (
        "中文字幕" in title
        or "中字" in title
        or re.search(r"(?<![A-Z])C(?![A-Z])", t)
        or re.search(r"\dC\b", t)
    ):
        tags.append("C")
    # UC already implies U+C
    if "UC" in tags:
        tags = ["UC"] + [x for x in tags if x not in ("UC", "U", "C")]
    return tags


def tag_tier(tags: list[str]) -> int:
    if "UC" in tags:
        return TIER_UC
    if "U" in tags:
        return TIER_U
    if "C" in tags:
        return TIER_C
    return TIER_NONE


def is_pack(title: str, size_bytes: int | None) -> bool:
    if size_bytes is not None and size_bytes >= PACK_SIZE:
        return True
    return bool(_PACK_RE.search(title))


def rank_key(item: dict, code: str) -> tuple:
    title = item.get("title") or ""
    size_bytes = item.get("size_bytes")
    if size_bytes is None:
        size_bytes = parse_size(item.get("size") or "") or 0
    heat = int(item.get("heat") or 0)
    tags = item.get("tags") or detect_tags(title)
    pack = is_pack(title, size_bytes)
    relevant = title_mentions_code(title, code)
    tier = TIER_PACK if pack else tag_tier(tags)
    # relevant titles first (0), then others
    return (0 if relevant else 1, tier, -heat, size_bytes or 10**18)


def sort_resources(items: list[dict], code: str) -> list[dict]:
    decorated = []
    for it in items:
        it = dict(it)
        it["tags"] = it.get("tags") or detect_tags(it.get("title") or "")
        it["_rank"] = rank_key(it, code)
        decorated.append(it)
    decorated.sort(key=lambda x: x["_rank"])
    out = []
    for i, it in enumerate(decorated, 1):
        it.pop("_rank", None)
        it["rank"] = i
        out.append(it)
    return out

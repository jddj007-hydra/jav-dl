from __future__ import annotations

import asyncio
import re

from app.config import Settings
from app.ranking import heat_key
from app.sources.clm import MagnetSearchError, search_magnets

_APOSTROPHE = re.compile(r"[’']")
_WORD = re.compile(r"[A-Za-z0-9]+")
_STOP = {
    "the", "and", "with", "for", "vol", "volume", "part", "episode",
    "scene", "from", "into", "your", "her", "his", "you", "that", "this",
}


def words(text: str) -> list[str]:
    cleaned = _APOSTROPHE.sub("", text or "")
    out: list[str] = []
    for word in _WORD.findall(cleaned):
        if len(word) < 3 or word.isdigit() or word.lower() in _STOP:
            continue
        out.append(word)
    return out


def western_search_terms(
    site: str,
    title: str,
    performers: list[str] | None = None,
) -> list[str]:
    """CLM queries for a western scene. Studio name only.

    Official titles are AND-ed by the search site and rarely match torrent names.
    """
    del title, performers
    site_words = words(site)[:4]
    if not site_words:
        return []
    spaced = " ".join(site_words)
    compact = "".join(site_words)
    terms = [spaced]
    if compact.lower() != spaced.lower() and len(compact) >= 4:
        terms.append(compact)
    return terms


def _has_token(hay: str, token: str) -> bool:
    return re.search(rf"(^|[^a-z0-9]){re.escape(token.lower())}([^a-z0-9]|$)", hay) is not None


def rank_western_magnets(
    items: list[dict],
    site: str,
    title: str,
    performers: list[str] | None = None,
) -> tuple[list[dict], str]:
    title_tokens = [word.lower() for word in words(title)]
    site_tokens = [word.lower() for word in words(site)]
    distinctive = [token for token in title_tokens if token not in site_tokens]
    person_tokens: list[str] = []
    named_people = [name for name in (performers or []) if len(words(name)) >= 2]
    for name in named_people:
        person_tokens.extend(word.lower() for word in words(name))
    compact_site = "".join(site_tokens)
    scored: list[dict] = []
    for item in items:
        raw = _APOSTROPHE.sub("", (item.get("title") or "").lower())
        hay = re.sub(r"[^a-z0-9]+", " ", raw)
        compact_hay = re.sub(r"[^a-z0-9]+", "", raw)
        title_hits = sum(1 for token in distinctive if _has_token(hay, token))
        person_hits = sum(1 for token in person_tokens if _has_token(hay, token))
        site_hits = sum(1 for token in site_tokens if _has_token(hay, token))
        if site_tokens and site_hits < len(site_tokens) and compact_site not in compact_hay:
            continue
        row = dict(item)
        row["_hits"] = (title_hits, person_hits, site_hits)
        scored.append(row)
    pool = scored
    def _strong(row: dict) -> int:
        title_hits, person_hits, _site_hits = row["_hits"]
        if title_hits >= 2 or (title_hits >= 1 and person_hits > 0):
            return 1
        return 0

    pool.sort(key=lambda row: (
        heat_key(row)[0],
        -_strong(row),
        -row["_hits"][0],
        -row["_hits"][1],
        heat_key(row)[1],
    ))
    match = "title" if pool and _strong(pool[0]) else ("site" if pool else "none")
    out: list[dict] = []
    for index, row in enumerate(pool[:24], 1):
        row.pop("_hits", None)
        row.pop("_kind", None)
        row["pack"] = bool(heat_key(row)[0])
        row["rank"] = index
        out.append(row)
    return out, match


async def collect_western_magnets(
    settings: Settings,
    site: str,
    title: str,
    performers: list[str] | None = None,
) -> tuple[list[dict], str, str | None]:
    terms = western_search_terms(site, title, performers)
    if not terms:
        return [], "none", None
    chunks = await asyncio.gather(
        *[search_magnets(settings, term, pages=2) for term in terms],
        return_exceptions=True,
    )
    merged: list[dict] = []
    seen: set[str] = set()
    error: str | None = None
    for chunk in chunks:
        if isinstance(chunk, MagnetSearchError):
            error = str(chunk)
            continue
        if isinstance(chunk, Exception):
            error = f"磁力猫请求失败: {chunk}"
            continue
        for item in chunk:
            info_hash = item.get("info_hash") or ""
            if not info_hash or info_hash in seen:
                continue
            seen.add(info_hash)
            merged.append(item)
    items, match = rank_western_magnets(merged, site, title, performers)
    if items:
        error = None
    return items, match, error

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
    """Short CLM queries. Full official titles are AND-ed and usually miss."""
    title_words = words(title)[:4]
    site_words = words(site)[:3]
    site_set = {word.lower() for word in site_words}
    distinctive = [word for word in title_words if word.lower() not in site_set]
    people = [" ".join(words(name)[:3]) for name in (performers or [])]
    people = [name for name in people if len(words(name)) >= 2]
    terms: list[str] = []

    def add(query: str) -> None:
        query = " ".join(query.split())
        if len(query) < 3:
            return
        if query.lower() in {item.lower() for item in terms}:
            return
        terms.append(query)

    if len(distinctive) >= 2:
        add(" ".join(distinctive))
    elif distinctive and site_words:
        add(" ".join(site_words[:2] + distinctive))
    elif distinctive:
        add(distinctive[0])
    if people and distinctive:
        add(f"{people[0]} {distinctive[-1]}")
    elif people and not distinctive:
        add(people[0])
    if site_words and not distinctive:
        add(" ".join(site_words))
    return terms[:3]


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
    need = 2 if len(distinctive) >= 3 else len(distinctive)
    scored: list[dict] = []
    for item in items:
        hay = re.sub(r"[^a-z0-9]+", " ", _APOSTROPHE.sub("", (item.get("title") or "").lower()))
        title_hits = sum(1 for token in distinctive if _has_token(hay, token))
        person_hits = sum(1 for token in person_tokens if _has_token(hay, token))
        site_hits = sum(1 for token in site_tokens if _has_token(hay, token))
        title_ok = need > 0 and title_hits >= need
        performer_and_title = person_hits > 0 and title_hits > 0
        if title_ok or performer_and_title:
            kind = "title"
        elif person_hits >= 2:
            kind = "performer"
        elif site_hits and not distinctive:
            kind = "site"
        else:
            kind = "weak"
        row = dict(item)
        row["_hits"] = (title_hits, person_hits, site_hits)
        row["_kind"] = kind
        scored.append(row)
    title_rows = [row for row in scored if row["_kind"] == "title"]
    if any(row["_hits"][1] > 0 for row in title_rows):
        title_rows = [row for row in title_rows if row["_hits"][1] > 0]
    performer_rows = [row for row in scored if row["_kind"] == "performer"]
    site_rows = [row for row in scored if row["_kind"] == "site"]
    pool = title_rows or performer_rows or site_rows
    match = pool[0]["_kind"] if pool else "none"
    pool.sort(key=lambda row: (
        heat_key(row)[0],
        -row["_hits"][0],
        -row["_hits"][1],
        -row["_hits"][2],
        heat_key(row)[1],
    ))
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
        *[search_magnets(settings, term, pages=1) for term in terms],
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

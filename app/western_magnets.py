from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path

from app.config import Settings
from app.ranking import heat_key
from app.sources.clm import MagnetSearchError, search_magnets

_APOSTROPHE = re.compile(r"[’']")
_WORD = re.compile(r"[A-Za-z0-9]+")
_RELEASE_DATE = re.compile(
    r"(?<![0-9])(?:(?P<y4>20[0-2][0-9])|(?P<y2>[0-9]{2}))[.\- ]"
    r"(?P<month>0[1-9]|1[0-2])[.\- ](?P<day>0[1-9]|[12][0-9]|3[01])(?![0-9])"
)
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


_WESTERN_HEAD = re.compile(
    r"^[A-Za-z][A-Za-z0-9]*[.\s_-]+"
    r"(?:(?:19|20)\d{2}|\d{2})[.\- ]"
    r"(?:0[1-9]|1[0-2])[.\- ]"
    r"(?:0[1-9]|[12]\d|3[01])(?!\d)"
)
_LEADING_NOISE = re.compile(r"^(?:\[[^\]]*\]|[^\s@/\\]{1,48}@)+")
_RELEASE_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_GROUP_SUFFIX = re.compile(r"-[A-Z0-9]{2,8}$")
_RELEASE_SUFFIXES = {".mp4", ".mkv", ".avi", ".wmv", ".ts", ".mov", ".m4v", ".webm"}


def release_text(name: str) -> str:
    """Drop watermarks, subtitle tags, and the trailing release group."""
    text = (name or "").strip()
    if Path(text).suffix.lower() in _RELEASE_SUFFIXES:
        text = Path(text).stem
    previous = None
    while text and previous != text:
        previous = text
        text = _LEADING_NOISE.sub("", text).strip()
    text = _RELEASE_BRACKETS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _GROUP_SUFFIX.sub("", text).strip(" .")
    return text


def is_western_release_name(name: str) -> bool:
    """Scene releases look like Site.YY.MM.DD, which is not a JAV code."""
    text = (name or "").strip()
    if not text:
        return False
    from app.codes import normalize_code

    if normalize_code(Path(text).stem):
        return False
    cleaned = release_text(text)
    if not cleaned or normalize_code(cleaned):
        return False
    return bool(_WESTERN_HEAD.match(cleaned))


def split_release_name(text: str) -> tuple[str, list[str]] | None:
    """Site plus date, then the tokens that follow it."""
    match = _RELEASE_DATE.search(text or "")
    if not match:
        return None
    head = (text or "")[: match.end()].strip(" .")
    tail = [tok for tok in re.split(r"[.\s_-]+", (text or "")[match.end():].strip()) if tok]
    if not head:
        return None
    return head, tail


def parse_release_date(title: str) -> str | None:
    """Date embedded in a scene release name, not the torrent upload time."""
    match = _RELEASE_DATE.search(title or "")
    if not match:
        return None
    if match.group("y4"):
        year = int(match.group("y4"))
    else:
        short = int(match.group("y2"))
        year = 1900 + short if short >= 70 else 2000 + short
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _date_gap(scene_date: str, release_date: str | None) -> int | None:
    if not release_date:
        return None
    try:
        left = date.fromisoformat(scene_date[:10])
        right = date.fromisoformat(release_date[:10])
    except ValueError:
        return None
    return abs((left - right).days)


def _studio_forms(site: str) -> list[str]:
    site_words = words(site)[:4]
    if not site_words:
        return []
    compact = "".join(site_words)
    spaced = " ".join(site_words)
    forms = [compact]
    if spaced.lower() != compact.lower():
        forms.append(spaced)
    return forms


def _date_stamps(scene_date: str) -> list[str]:
    try:
        parsed = date.fromisoformat((scene_date or "")[:10])
    except ValueError:
        return []
    year = f"{parsed.year:04d}"
    return [
        f"{year[2:]}.{parsed.month:02d}.{parsed.day:02d}",
        f"{year}.{parsed.month:02d}.{parsed.day:02d}",
    ]


def western_search_terms(
    site: str,
    title: str,
    performers: list[str] | None = None,
    scene_date: str = "",
) -> list[str]:
    """Studio plus the release date used in scene filenames.

    Namer and Stash match `Site.YY.MM.DD`, not the torrent's upload time.
    """
    del title, performers
    forms = _studio_forms(site)
    if not forms:
        return []
    terms: list[str] = []
    for stamp in _date_stamps(scene_date):
        terms.append(f"{forms[0]} {stamp}")
    terms.append(forms[0])
    return terms


def western_fallback_terms(
    site: str,
    title: str,
    performers: list[str] | None = None,
) -> list[str]:
    """Studio plus a performer or one title word, for when the release date is absent."""
    forms = _studio_forms(site)
    if not forms:
        return []
    studio = forms[0]
    terms: list[str] = []
    for name in (performers or [])[:2]:
        bits = words(name)
        if len(bits) >= 2:
            terms.append(f"{studio} {bits[0]} {bits[1]}")
        elif len(bits) == 1 and len(bits[0]) >= 4:
            terms.append(f"{studio} {bits[0]}")
    site_words = {word.lower() for word in words(site)}
    distinctive = [word for word in words(title) if word.lower() not in site_words and len(word) >= 5]
    distinctive.sort(key=len, reverse=True)
    if distinctive:
        terms.append(f"{studio} {distinctive[0]}")
    out: list[str] = []
    for term in terms:
        if term not in out:
            out.append(term)
    return out[:3]


def _has_token(hay: str, token: str) -> bool:
    return re.search(rf"(^|[^a-z0-9]){re.escape(token.lower())}([^a-z0-9]|$)", hay) is not None


def rank_western_magnets(
    items: list[dict],
    site: str,
    title: str,
    performers: list[str] | None = None,
    scene_date: str = "",
    related_only: bool = False,
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
        released = parse_release_date(item.get("title") or "")
        gap = _date_gap(scene_date, released) if scene_date else None
        row = dict(item)
        row["release_date"] = released or ""
        row["_hits"] = (title_hits, person_hits, site_hits)
        row["_gap"] = gap if gap is not None else 10_000
        scored.append(row)
    def _strong(row: dict) -> int:
        title_hits, person_hits, _site_hits = row["_hits"]
        if title_hits >= 2 or (title_hits >= 1 and person_hits > 0):
            return 1
        return 0

    close = [row for row in scored if scene_date and row["_gap"] <= 1]
    if close:
        pool = close
    elif related_only:
        pool = [row for row in scored if _strong(row)]
    else:
        pool = scored
    limit = 24 if close else (8 if related_only else 24)

    pool.sort(key=lambda row: (
        heat_key(row)[0],
        row["_gap"],
        -_strong(row),
        -row["_hits"][0],
        -row["_hits"][1],
        heat_key(row)[1],
    ))
    if close:
        match = "date"
    elif pool and _strong(pool[0]):
        match = "title"
    elif pool:
        match = "site"
    else:
        match = "none"
    out: list[dict] = []
    for index, row in enumerate(pool[:limit], 1):
        row.pop("_hits", None)
        row.pop("_gap", None)
        row.pop("_kind", None)
        row["pack"] = bool(heat_key(row)[0])
        row["rank"] = index
        out.append(row)
    return out, match


async def _search_terms(settings: Settings, terms: list[str], pages: int) -> tuple[list[dict], str | None]:
    if not terms:
        return [], None
    chunks = await asyncio.gather(
        *[search_magnets(settings, term, pages=pages) for term in terms],
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
    return merged, error


async def collect_western_magnets(
    settings: Settings,
    site: str,
    title: str,
    performers: list[str] | None = None,
    scene_date: str = "",
) -> tuple[list[dict], str, str | None]:
    terms = western_search_terms(site, title, performers, scene_date)
    if not terms:
        return [], "none", None
    date_terms = [term for term in terms if any(char.isdigit() for char in term)]
    studio_terms = [term for term in terms if term not in date_terms]
    merged, error = await _search_terms(settings, date_terms, pages=1)
    items, match = rank_western_magnets(merged, site, title, performers, scene_date)
    if match == "date":
        return items, match, None
    extra, extra_error = await _search_terms(
        settings,
        western_fallback_terms(site, title, performers) or studio_terms[:1],
        pages=1,
    )
    combined: list[dict] = []
    seen: set[str] = set()
    for item in [*merged, *extra]:
        info_hash = item.get("info_hash") or ""
        if not info_hash or info_hash in seen:
            continue
        seen.add(info_hash)
        combined.append(item)
    items, match = rank_western_magnets(
        combined,
        site,
        title,
        performers,
        scene_date,
        related_only=True,
    )
    if items:
        return items, match, None
    return [], "none", extra_error or error or "磁力猫里没有这个发行日的磁链"

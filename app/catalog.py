from __future__ import annotations

import json
from urllib.parse import quote

MOVIE_SORTS = {
    "release": "premiered",
    "added": "added_at",
    "code": "code",
    "title": "title",
}
SCENE_SORTS = {
    "release": "premiered",
    "added": "added_at",
    "title": "title",
    "site": "site",
}


def names(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item or "").strip()]
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _unix(value) -> int:
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _minutes(value) -> int | None:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return None
    return number or None


def poster_url(kind: str, path: str) -> str:
    if not path:
        return ""
    return f"/api/library/poster?kind={kind}&path={quote(path, safe='')}"


def movie_item(row: dict) -> dict:
    folder = row.get("path") or ""
    poster = row.get("poster") or ""
    return {
        "code": row.get("code") or "",
        "title": row.get("title") or "",
        "actors": names(row.get("actors")),
        "genres": names(row.get("genres")),
        "studio": row.get("studio") or "",
        "series": row.get("series") or "",
        "premiered": row.get("release_date") or "",
        "year_month": row.get("month") or "",
        "runtime_min": _minutes(row.get("runtime_min")),
        "has_sub": bool(row.get("has_sub")),
        "has_uncensored": bool(row.get("has_uncensored")),
        "has_cracked": bool(row.get("has_cracked")),
        "folder": folder,
        "video": row.get("video") or "",
        "video_count": int(row.get("video_count") or 0),
        "video_size": int(row.get("video_size") or 0),
        "poster": poster,
        "poster_url": poster_url("jav", folder) if poster or row.get("has_poster") else "",
        "outline": row.get("outline") or "",
        "added_at": row.get("added_at") or 0,
        "last_played_at": _unix(row.get("last_played_at")),
    }


def scene_item(row: dict) -> dict:
    path = row.get("path") or ""
    poster = row.get("poster") or ""
    folder, _, name = path.rpartition("/")
    year = _minutes(row.get("year"))
    return {
        "title": row.get("title") or "",
        "site": row.get("studio") or "",
        "performers": names(row.get("actors")),
        "premiered": row.get("release_date") or "",
        "year": year,
        "runtime_min": _minutes(row.get("runtime_min")),
        "resolution": row.get("resolution") or "",
        "video": path,
        "folder": folder,
        "poster": poster,
        "poster_url": poster_url("western", path) if poster or row.get("has_poster") else "",
        "release_name": name.rsplit(".", 1)[0] if name else "",
        "tpdb_id": row.get("tpdb_id") or "",
        "added_at": row.get("added_at") or 0,
        "last_played_at": _unix(row.get("last_played_at")),
    }


def query_movies(rows: list[dict], spec: dict) -> dict:
    items = [movie_item(row) for row in rows]
    matched = [item for item in items if _movie_matches(item, spec)]
    return _page(items, matched, spec, "code")


def query_scenes(rows: list[dict], spec: dict) -> dict:
    items = [scene_item(row) for row in rows]
    matched = [item for item in items if _scene_matches(item, spec)]
    return _page(items, matched, spec, "video")


def movie_facets(rows: list[dict]) -> dict:
    items = [movie_item(row) for row in rows]
    studios: dict[str, int] = {}
    months: set[str] = set()
    actors: set[str] = set()
    for item in items:
        if item["studio"]:
            studios[item["studio"]] = studios.get(item["studio"], 0) + 1
        if item["year_month"]:
            months.add(item["year_month"])
        actors.update(item["actors"])
    return {
        "studios": [
            {"name": name, "count": studios[name]}
            for name in sorted(studios, key=str.casefold)
        ],
        "year_months": sorted(months, reverse=True),
        "actors": sorted(actors, key=str.casefold),
    }


def scene_facets(rows: list[dict]) -> dict:
    items = [scene_item(row) for row in rows]
    sites: dict[str, int] = {}
    years: set[int] = set()
    performers: set[str] = set()
    for item in items:
        if item["site"]:
            sites[item["site"]] = sites.get(item["site"], 0) + 1
        if item["year"]:
            years.add(item["year"])
        performers.update(item["performers"])
    return {
        "sites": [
            {"name": name, "count": sites[name]}
            for name in sorted(sites, key=str.casefold)
        ],
        "years": sorted(years, reverse=True),
        "performers": sorted(performers, key=str.casefold),
    }


def _page(all_items: list[dict], matched: list[dict], spec: dict, tie: str) -> dict:
    field = spec["sort_field"]
    matched.sort(
        key=lambda item: (_sort_value(item.get(field)), str(item.get(tie) or "").casefold()),
        reverse=bool(spec["descending"]),
    )
    page = matched[spec["offset"]:]
    if spec["limit"]:
        page = page[: spec["limit"]]
    return {
        "total": len(all_items),
        "matched": len(matched),
        "paths": "relative",
        "items": page,
    }


def _sort_value(value):
    if isinstance(value, str):
        return value.casefold()
    return value or 0


def _movie_matches(item: dict, spec: dict) -> bool:
    if spec["year_month"] and item["year_month"] != spec["year_month"]:
        return False
    if spec["studio"] and item["studio"].casefold() != spec["studio"].casefold():
        return False
    if spec["subtitle"] and not item["has_sub"]:
        return False
    if spec["uncensored"] and not item["has_uncensored"]:
        return False
    if spec["cracked"] and not item["has_cracked"]:
        return False
    if spec["actor"] and not _contains(item["actors"], spec["actor"]):
        return False
    if spec["genre"] and not _contains(item["genres"], spec["genre"]):
        return False
    if spec["q"] and not _movie_text(item, spec["q"]):
        return False
    return True


def _scene_matches(item: dict, spec: dict) -> bool:
    if spec["site"] and item["site"].casefold() != spec["site"].casefold():
        return False
    if spec["year"] and item["year"] != spec["year"]:
        return False
    if spec["performer"] and not _contains(item["performers"], spec["performer"]):
        return False
    if spec["q"] and not _scene_text(item, spec["q"]):
        return False
    return True


def _contains(values: list[str], needle: str) -> bool:
    folded = needle.casefold()
    return any(folded in value.casefold() for value in values)


def _movie_text(item: dict, needle: str) -> bool:
    haystack = "\n".join([
        item["code"],
        item["title"],
        item["studio"],
        item["outline"],
        " ".join(item["actors"]),
        " ".join(item["genres"]),
    ]).casefold()
    return needle.casefold() in haystack


def _scene_text(item: dict, needle: str) -> bool:
    haystack = "\n".join([
        item["site"],
        item["title"],
        item["release_name"],
        " ".join(item["performers"]),
    ]).casefold()
    return needle.casefold() in haystack

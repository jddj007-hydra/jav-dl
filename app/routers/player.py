from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.catalog import (
    MOVIE_SORTS,
    SCENE_SORTS,
    library_stamp,
    movie_facets,
    query_movies,
    query_scenes,
    scene_facets,
)
from app.models import PlayMark
from app.suck import parse_suck

router = APIRouter()


def _bounds(offset: int, limit: int) -> tuple[int, int]:
    if offset < 0 or limit < 0 or limit > 5000:
        raise HTTPException(400, "分页无效")
    return offset, limit


def _sort(name: str, allowed: dict[str, str]) -> str:
    key = (name or "release").strip().lower()
    if key not in allowed:
        raise HTTPException(400, "排序无效")
    return allowed[key]


def _format(name: str) -> str:
    key = (name or "all").strip().lower() or "all"
    if key not in ("flat", "vr", "all"):
        raise HTTPException(400, "格式无效")
    return key


async def _catalog_rows(request: Request) -> tuple[list[dict], list[dict]]:
    db = request.app.state.db
    return await db.list_library(), await db.list_western()


def _with_stamp(movie_rows: list[dict], scene_rows: list[dict], body: dict) -> dict:
    return {**body, **library_stamp(movie_rows, scene_rows)}


@router.get("/api/player/stamp")
async def player_stamp(request: Request):
    movies, scenes = await _catalog_rows(request)
    return library_stamp(movies, scenes)


@router.get("/api/player/movies")
async def player_movies(
    request: Request,
    q: str = "",
    actor: str = "",
    genre: str = "",
    studio: str = "",
    year_month: str = "",
    subtitle: bool = False,
    uncensored: bool = False,
    cracked: bool = False,
    sort: str = "release",
    desc: bool = True,
    offset: int = 0,
    limit: int = 0,
    format: str = "all",
):
    offset, limit = _bounds(offset, limit)
    movies, scenes = await _catalog_rows(request)
    return _with_stamp(movies, scenes, query_movies(movies, {
        "q": q.strip(),
        "actor": actor.strip(),
        "genre": genre.strip(),
        "studio": studio.strip(),
        "year_month": year_month.strip(),
        "subtitle": subtitle,
        "uncensored": uncensored,
        "cracked": cracked,
        "format": _format(format),
        "sort_field": _sort(sort, MOVIE_SORTS),
        "descending": desc,
        "offset": offset,
        "limit": limit,
    }))


@router.post("/api/player/played")
async def player_played(body: PlayMark, request: Request):
    try:
        kind, key = parse_suck(body.kind, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db = request.app.state.db
    if kind == "jav":
        known = await db.get_library(key)
    else:
        known = (await db.western_by_ids([key])).get(key)
    if not known:
        raise HTTPException(404, "片库里没有这部")
    played = await db.mark_played(kind, key)
    return {"kind": kind, "key": key, "last_played_at": played}


@router.get("/api/player/movies/facets")
async def player_movie_facets(request: Request):
    return movie_facets(await request.app.state.db.list_library())


@router.get("/api/player/scenes")
async def player_scenes(
    request: Request,
    q: str = "",
    performer: str = "",
    site: str = "",
    year: int = 0,
    sort: str = "release",
    desc: bool = True,
    offset: int = 0,
    limit: int = 0,
    format: str = "all",
):
    offset, limit = _bounds(offset, limit)
    if year < 0:
        raise HTTPException(400, "年份无效")
    movies, scenes = await _catalog_rows(request)
    return _with_stamp(movies, scenes, query_scenes(scenes, {
        "q": q.strip(),
        "performer": performer.strip(),
        "site": site.strip(),
        "year": year,
        "format": _format(format),
        "sort_field": _sort(sort, SCENE_SORTS),
        "descending": desc,
        "offset": offset,
        "limit": limit,
    }))


@router.get("/api/player/scenes/facets")
async def player_scene_facets(request: Request):
    return scene_facets(await request.app.state.db.list_western())

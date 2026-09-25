from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.follow import FOLLOW_LIMIT, check_sub, new_subscription, resolve_target
from app.sources.javbus import MetadataError
from app.sources.tpdb import TpdbError

router = APIRouter()


class FollowCreate(BaseModel):
    kind: str
    name: str = ""
    target: str = ""
    auto: bool = False
    want_uc: bool = False
    want_c: bool = False
    max_gb: int = Field(default=0, ge=0)


class FollowCheck(BaseModel):
    id: str = ""


@router.get("/api/subscriptions")
async def list_subscriptions(request: Request):
    db = request.app.state.db
    return {
        "items": await db.list_subscriptions(),
        "hits": await db.list_hits(),
        "unread": await db.count_unread_hits(),
    }


@router.post("/api/subscriptions")
async def create_subscription(request: Request, body: FollowCreate):
    db = request.app.state.db
    if len(await db.list_subscriptions()) >= FOLLOW_LIMIT:
        raise HTTPException(400, f"最多关注 {FOLLOW_LIMIT} 个")
    try:
        name, target = await resolve_target(
            request.app.state.settings, body.kind, body.name, body.target
        )
    except (ValueError, MetadataError, TpdbError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = new_subscription(
        body.kind,
        name,
        target,
        auto=body.auto,
        want_uc=body.want_uc,
        want_c=body.want_c,
        max_gb=body.max_gb,
    )
    await db.add_subscription(row)
    summary = await check_sub(request.app.state.jobs, row)
    saved = await db.get_subscription(row["id"])
    return {"item": saved, "check": summary}


@router.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(request: Request, sub_id: str):
    await request.app.state.db.delete_subscription(sub_id)
    return {"ok": True}


@router.post("/api/subscriptions/check")
async def check_subscriptions(request: Request, body: FollowCheck):
    jobs = request.app.state.jobs
    db = request.app.state.db
    if body.id:
        sub = await db.get_subscription(body.id)
        if not sub:
            raise HTTPException(404, "没有这个关注")
        subs = [sub]
    else:
        subs = await db.list_subscriptions()
    summaries = []
    for sub in subs:
        summaries.append({"id": sub["id"], **await check_sub(jobs, sub)})
    jobs._last_follow = time.time()
    return {"items": summaries, "unread": await db.count_unread_hits()}


@router.post("/api/subscriptions/hits/{hit_id}/read")
async def read_hit(request: Request, hit_id: str):
    await request.app.state.db.read_hit(hit_id)
    return {"ok": True, "unread": await request.app.state.db.count_unread_hits()}

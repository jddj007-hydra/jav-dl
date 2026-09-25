from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.pickfiles import aria_content_files


class Aria2Error(Exception):
    pass


def gid_is_gone(exc: BaseException) -> bool:
    return "not found" in str(exc).lower()


def _gid_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = value
    return [str(item).strip() for item in parts if str(item).strip()]


def _num(status: dict, key: str) -> int:
    try:
        return int(status.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def aggregate_status(gids: list[str], statuses: list[tuple[str, dict]]) -> dict:
    by_gid = {gid: status for gid, status in statuses}
    chosen = [(gid, by_gid[gid]) for gid in gids if gid in by_gid]
    if not chosen:
        raise Aria2Error("缺少 gid")
    names = [str(status.get("status") or "") for _, status in chosen]
    if any(name == "error" for name in names):
        status_name = "error"
    elif any(name == "active" for name in names):
        status_name = "active"
    elif names and all(name == "missing" for name in names):
        status_name = "missing"
    elif any(name in ("waiting", "missing") for name in names):
        status_name = "waiting"
    elif any(name == "paused" for name in names):
        status_name = "paused"
    elif all(name == "complete" for name in names):
        status_name = "complete"
    elif all(name == "removed" for name in names):
        status_name = "removed"
    else:
        status_name = names[0] or "waiting"
    error = ""
    for _, status in chosen:
        if status.get("errorMessage"):
            error = str(status["errorMessage"])
            break
    return {
        "gid": chosen[0][0],
        "status": status_name,
        "totalLength": str(sum(_num(status, "totalLength") for _, status in chosen)),
        "completedLength": str(sum(_num(status, "completedLength") for _, status in chosen)),
        "downloadSpeed": str(sum(_num(status, "downloadSpeed") for _, status in chosen)),
        "errorMessage": error if status_name == "error" else "",
        "connections": sum(_num(status, "connections") for _, status in chosen),
        "numSeeders": max(_num(status, "numSeeders") for _, status in chosen),
        "gids": [gid for gid, _ in chosen],
        "stored": ",".join(gid for gid, _ in chosen),
    }


class Aria2:
    def __init__(self, settings: Settings):
        self.rpc = settings.aria2_rpc
        self.secret = settings.aria2_secret

    def _token(self) -> str:
        return f"token:{self.secret}" if self.secret else ""

    async def call(self, method: str, params: list | None = None) -> Any:
        payload_params: list = []
        if self.secret:
            payload_params.append(self._token())
        if params:
            payload_params.extend(params)
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": payload_params,
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(self.rpc, json=body)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            raise Aria2Error(
                "连不上 aria2。本机先启动 aria2c，或使用 docker compose 一起拉起 web 和 aria2"
            ) from e
        if "error" in data:
            err = data["error"]
            raise Aria2Error(err.get("message") if isinstance(err, dict) else str(err))
        return data.get("result")

    async def version(self) -> str | None:
        try:
            result = await self.call("aria2.getVersion")
        except Aria2Error:
            return None
        if isinstance(result, dict):
            return result.get("version")
        return str(result) if result else None

    async def add_magnet(self, magnet: str, dest: str) -> str:
        opts = {
            "dir": dest,
            "pause": "false",
            "seed-time": "0",
            "follow-torrent": "true",
        }
        gid = await self.call("aria2.addUri", [[magnet], opts])
        if not gid:
            raise Aria2Error("aria2 未返回 gid")
        return str(gid)

    async def inspect_files(self, magnet: str, dest: str, *, timeout: float = 20.0) -> tuple[str, str, list[dict]]:
        """Add the magnet and pause it once the real file list is known.

        Returns (content gid, metadata gid, files). The caller resumes it,
        or removes it if the user cancels.
        """
        meta = await self.add_magnet(magnet, dest)
        content = meta
        hops = 0
        deadline = time.monotonic() + timeout
        while True:
            status = await self.tell(content)
            followed = _gid_list(status.get("followedBy"))
            nxt = followed[0] if followed else ""
            if nxt and nxt != content and hops < 4:
                content = nxt
                hops += 1
                continue
            files = aria_content_files(status)
            if files is not None:
                await self.pause(content)
                return content, meta, files
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.4)
        await self._drop_gid(content)
        if content != meta:
            await self._drop_gid(meta)
        raise Aria2Error("暂时读不到种子里的文件")

    async def _drop_gid(self, gid: str) -> None:
        try:
            await self.remove(gid)
        except Aria2Error:
            pass

    async def change_option(self, gid: str, options: dict) -> None:
        await self.call("aria2.changeOption", [gid, options])

    async def tell(self, gid: str) -> dict:
        keys = [
            "gid",
            "status",
            "totalLength",
            "completedLength",
            "downloadSpeed",
            "uploadSpeed",
            "files",
            "errorMessage",
            "dir",
            "connections",
            "numSeeders",
            "bittorrent",
            "followedBy",
        ]
        result = await self.call("aria2.tellStatus", [gid, keys])
        return result if isinstance(result, dict) else {}

    async def resolve(self, gid: str) -> dict:
        """Follow magnet metadata gids to the content download."""
        current = _gid_list(gid)
        if not current:
            raise Aria2Error("缺少 gid")
        statuses: list[tuple[str, dict]] = []
        followed_once = False
        for _ in range(4):
            statuses = []
            nxt: list[str] = []
            seen: set[str] = set()
            followed_any = False
            for item in current:
                if item in seen:
                    continue
                seen.add(item)
                try:
                    status = await self.tell(item)
                except Aria2Error as exc:
                    if not gid_is_gone(exc):
                        raise
                    statuses.append((item, {"gid": item, "status": "missing", "errorMessage": str(exc)}))
                    nxt.append(item)
                    continue
                statuses.append((item, status))
                children = _gid_list(status.get("followedBy"))
                if status.get("status") == "complete" and children:
                    followed_any = True
                    nxt.extend(children)
                else:
                    nxt.append(item)
            current = nxt or current
            if not followed_any:
                break
            followed_once = True
        result = aggregate_status(current, statuses)
        if result["status"] == "missing" and followed_once:
            result["status"] = "waiting"
            result["errorMessage"] = ""
        return result

    async def pause(self, gid: str) -> None:
        await self.call("aria2.pause", [gid])

    async def resume(self, gid: str) -> None:
        await self.call("aria2.unpause", [gid])

    async def remove(self, gid: str) -> None:
        last: Aria2Error | None = None
        for method in ("aria2.remove", "aria2.forceRemove"):
            try:
                await self.call(method, [gid])
            except Aria2Error as exc:
                last = exc
                if gid_is_gone(exc):
                    return
                continue
            return
        if last:
            raise last

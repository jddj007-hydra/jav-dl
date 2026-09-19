from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import Settings


class Aria2Error(Exception):
    pass


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
        ]
        result = await self.call("aria2.tellStatus", [gid, keys])
        return result if isinstance(result, dict) else {}

    async def pause(self, gid: str) -> None:
        await self.call("aria2.pause", [gid])

    async def resume(self, gid: str) -> None:
        await self.call("aria2.unpause", [gid])

    async def remove(self, gid: str) -> None:
        try:
            await self.call("aria2.remove", [gid])
        except Aria2Error:
            await self.call("aria2.forceRemove", [gid])

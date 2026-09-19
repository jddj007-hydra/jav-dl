from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.downloader.aria2 import Aria2, Aria2Error
from app.downloader.xunlei import Xunlei, XunleiError
from app.textutil import format_size
from app.trackers import magnet_for

BackendError = (Aria2Error, XunleiError)

ACTIVE = {"active", "waiting", "paused", "downloading", "queued"}
DONE = {"complete", "error", "removed", "cancelled"}


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _map_status(aria_status: str | None, fallback: str) -> str:
    return {
        "active": "downloading",
        "waiting": "queued",
        "paused": "paused",
        "complete": "complete",
        "error": "error",
        "removed": "cancelled",
    }.get(aria_status or "", fallback)


def _eta(total: int, done: int, speed: int) -> str:
    if speed <= 0 or total <= 0 or done >= total:
        return ""
    remain = (total - done) / speed
    if remain < 60:
        return f"{int(remain)}s"
    if remain < 3600:
        return f"{int(remain // 60)}m"
    return f"{int(remain // 3600)}h{int((remain % 3600) // 60):02d}m"


def cleanup_dir(dest: str) -> None:
    root = Path(dest)
    if not root.exists():
        return
    for pattern in ("*.torrent", "*.aria2", "manko.fun.*"):
        for p in root.glob(pattern):
            try:
                p.unlink()
            except OSError:
                pass
    for child in list(root.rglob("*")):
        if child.is_file() and child.name.startswith("manko.fun"):
            try:
                child.unlink()
            except OSError:
                pass


class JobManager:
    def __init__(self, settings: Settings, db: Database, aria2: Aria2, xunlei: Xunlei | None = None):
        self.settings = settings
        self.db = db
        self.aria2 = aria2
        self.xunlei = xunlei or Xunlei(settings)

    def _use_xunlei(self) -> bool:
        return (self.settings.downloader or "aria2").strip().lower() == "xunlei"

    async def _add_magnet(self, magnet: str, dest: str) -> str:
        if self._use_xunlei():
            return await self.xunlei.add_magnet(magnet, dest)
        return await self.aria2.add_magnet(magnet, dest)

    async def _tell(self, gid: str) -> dict:
        if self._use_xunlei():
            return await self.xunlei.tell(gid)
        return await self.aria2.tell(gid)

    async def enqueue(self, code: str, info_hash: str, title: str) -> dict:
        info_hash = info_hash.lower()
        existing = await self.db.find_job_by_hash(info_hash)
        if existing and existing["status"] not in ("error", "cancelled", "complete"):
            return await self.public(existing)

        dest = str(self.settings.download_dir / code)
        if not self._use_xunlei():
            Path(dest).mkdir(parents=True, exist_ok=True)
        magnet = magnet_for(info_hash, title or code)
        gid = await self._add_magnet(magnet, dest)
        now = time.time()
        job = {
            "id": _new_id(),
            "code": code,
            "info_hash": info_hash,
            "title": title or code,
            "magnet": magnet,
            "gid": gid,
            "status": "queued",
            "dest": dest,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 0,
        }
        await self.db.insert_job(job)
        return await self.public(job)

    async def sync_one(self, job: dict) -> dict:
        gid = job.get("gid")
        if not gid or job.get("status") in ("cancelled",):
            return job
        try:
            st = await self._tell(gid)
        except BackendError as e:
            if job.get("status") in DONE:
                return job
            await self.db.update_job(job["id"], error=str(e))
            job = dict(job)
            job["error"] = str(e)
            return job
        status = _map_status(st.get("status"), job.get("status") or "queued")
        error = st.get("errorMessage") or job.get("error")
        fields: dict = {"status": status}
        if error:
            fields["error"] = error
        if status == "complete" and not job.get("cleaned"):
            cleanup_dir(job["dest"])
            fields["cleaned"] = 1
        await self.db.update_job(job["id"], **fields)
        job = dict(job)
        job.update(fields)
        job["_live"] = st
        return job

    async def sync_all(self) -> None:
        jobs = await self.db.list_jobs()
        for job in jobs:
            if job["status"] in ("complete", "cancelled") and job.get("cleaned"):
                continue
            try:
                await self.sync_one(job)
            except Exception:
                continue

    async def public(self, job: dict) -> dict:
        live = job.get("_live")
        if live is None and job.get("gid") and job.get("status") not in ("cancelled",):
            try:
                live = await self._tell(job["gid"])
            except BackendError:
                live = {}
        live = live or {}
        total = int(live.get("totalLength") or 0)
        done = int(live.get("completedLength") or 0)
        speed = int(live.get("downloadSpeed") or 0)
        pct = (done / total * 100) if total else 0.0
        status = _map_status(live.get("status"), job.get("status") or "queued")
        return {
            "id": job["id"],
            "code": job["code"],
            "info_hash": job["info_hash"],
            "title": job["title"],
            "status": status,
            "dest": job["dest"],
            "error": live.get("errorMessage") or job.get("error"),
            "progress": round(pct, 1),
            "downloaded": format_size(done) if done else "0 B",
            "total": format_size(total) if total else "?",
            "speed": f"{format_size(speed)}/s" if speed else "0 B/s",
            "eta": _eta(total, done, speed),
            "connections": int(live.get("connections") or 0),
            "seeders": int(live.get("numSeeders") or 0),
            "created_at": job.get("created_at"),
        }

    async def list_public(self) -> list[dict]:
        out = []
        for job in await self.db.list_jobs():
            try:
                job = await self.sync_one(job)
            except Exception:
                pass
            out.append(await self.public(job))
        return out

    async def pause(self, job_id: str) -> dict:
        job = await self._require(job_id)
        if job.get("gid"):
            if self._use_xunlei():
                await self.xunlei.pause(job["gid"])
            else:
                await self.aria2.pause(job["gid"])
        await self.db.update_job(job_id, status="paused")
        job["status"] = "paused"
        return await self.public(job)

    async def resume(self, job_id: str) -> dict:
        job = await self._require(job_id)
        if job.get("gid"):
            if self._use_xunlei():
                await self.xunlei.resume(job["gid"])
            else:
                await self.aria2.resume(job["gid"])
        await self.db.update_job(job_id, status="downloading")
        job["status"] = "downloading"
        return await self.public(job)

    async def cancel(self, job_id: str) -> dict:
        job = await self._require(job_id)
        if job.get("gid"):
            try:
                if self._use_xunlei():
                    await self.xunlei.remove(job["gid"])
                else:
                    await self.aria2.remove(job["gid"])
            except BackendError:
                pass
        await self.db.update_job(job_id, status="cancelled")
        job["status"] = "cancelled"
        return await self.public(job)

    async def _require(self, job_id: str) -> dict:
        job = await self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        return job

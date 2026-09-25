from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from app.codes import normalize_code
from app.config import Settings
from app.db import Database
from app.downloader.aria2 import Aria2, Aria2Error
from app.downloader.xunlei import Xunlei, XunleiError
from app.scrape import (
    ScrapeError,
    find_code_videos,
    has_incomplete_files,
    is_incomplete,
    list_ready_sources,
    newest_mtime,
    scrape_job,
)
from app.western_archive import (
    find_western_videos,
    list_ready_western,
    read_sidecar,
    scrape_western_job,
    scrape_western_source,
)
from app.textutil import format_size
from app.trackers import magnet_for

BackendError = (Aria2Error, XunleiError)

ACTIVE = {"active", "waiting", "paused", "downloading", "queued"}
DONE = {"complete", "error", "removed", "cancelled"}
SCRAPE_DONE = {"archived", "skipped"}
SCRAPE_RETRY_AFTER = 300
WATCH_EVERY = 15


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
    def __init__(
        self,
        settings: Settings,
        db: Database,
        aria2: Aria2,
        xunlei: Xunlei | None = None,
        library=None,
    ):
        self.settings = settings
        self.db = db
        self.aria2 = aria2
        self.xunlei = xunlei or Xunlei(settings)
        self.library = library
        self._last_watch = 0.0
        self._watch_fail: dict[str, float] = {}
        self._scrape_lock = asyncio.Lock()

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

    def _dest_for(self, code: str, dest_rel: str | None) -> Path:
        rel = Path(dest_rel or code)
        if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
            raise Aria2Error("目录无效")
        return self.settings.download_dir.joinpath(rel)

    async def enqueue(
        self,
        code: str,
        info_hash: str,
        title: str,
        *,
        dest_rel: str | None = None,
    ) -> dict:
        info_hash = info_hash.lower()
        existing = await self.db.find_job_by_hash(info_hash)
        if existing and existing["status"] not in ("error", "cancelled", "complete"):
            return await self.public(existing)

        dest_path = self._dest_for(code, dest_rel)
        dest = str(dest_path)
        if not self._use_xunlei():
            dest_path.mkdir(parents=True, exist_ok=True)
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
            try:
                if job.get("status") == "cancelled":
                    continue
                needs_tell = not (job.get("status") == "complete" and job.get("cleaned"))
                if needs_tell:
                    job = await self.sync_one(job)
                scrape_st = job.get("scrape_status") or ""
                if (
                    job.get("status") == "complete"
                    and job.get("cleaned")
                    and scrape_st not in SCRAPE_DONE
                ):
                    await self.maybe_scrape(job)
            except Exception:
                continue
        now = time.time()
        if now - self._last_watch >= WATCH_EVERY:
            self._last_watch = now
            try:
                await self.watch_western()
            except Exception:
                pass
            try:
                await self.watch_downloads()
            except Exception:
                pass

    async def _mark_waiting(self, job: dict) -> dict:
        if job.get("scrape_status") != "waiting":
            await self.db.update_job(job["id"], scrape_status="waiting", scrape_error=None)
            job["scrape_status"] = "waiting"
            job["scrape_error"] = None
        return job

    async def _mark_archived(self, job: dict, path: str) -> dict:
        await self.db.update_job(
            job["id"],
            scrape_status="archived",
            archive_path=path,
            scrape_error=None,
        )
        job["scrape_status"] = "archived"
        job["archive_path"] = path
        job["scrape_error"] = None
        return job

    async def maybe_scrape(self, job: dict) -> dict:
        status = job.get("scrape_status") or ""
        if status in SCRAPE_DONE:
            return job
        if not self.settings.scrape_enabled:
            if status != "skipped":
                await self.db.update_job(job["id"], scrape_status="skipped", scrape_error=None)
                job["scrape_status"] = "skipped"
                job["scrape_error"] = None
            return job
        if status == "error":
            updated = float(job.get("updated_at") or 0)
            if time.time() - updated < SCRAPE_RETRY_AFTER:
                return job
        if not normalize_code(job.get("code") or ""):
            info = read_sidecar(Path(job.get("dest") or ""))
            if self.settings.western_root and info and info.get("kind") == "western":
                return await self._scrape_western(job, info)
            if status != "skipped":
                await self.db.update_job(job["id"], scrape_status="skipped", scrape_error=None)
                job["scrape_status"] = "skipped"
                job["scrape_error"] = None
            return job

        dest = Path(job.get("dest") or "")
        settle = max(0, int(self.settings.scrape_settle_seconds))
        min_bytes = max(0, int(self.settings.scrape_min_mb) * 1024 * 1024)
        hit = None
        if self.library:
            hit = await self.library.get(job.get("code") or "")

        try:
            src, _videos = find_code_videos(
                job.get("code") or "",
                dest,
                self.settings.download_dir,
                min_bytes,
            )
        except ScrapeError:
            if hit and hit.get("has_video"):
                return await self._mark_archived(job, hit.get("path") or "")
            await self.db.update_job(
                job["id"],
                scrape_status="error",
                scrape_error="没有可归档的视频",
            )
            job["scrape_status"] = "error"
            job["scrape_error"] = "没有可归档的视频"
            return job

        if has_incomplete_files(src):
            return await self._mark_waiting(job)
        mtime = newest_mtime(src)
        if mtime and time.time() - mtime < settle:
            return await self._mark_waiting(job)

        try:
            async with self._scrape_lock:
                result = await scrape_job(self.settings, self.db, job)
        except ScrapeError as e:
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(e))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(e)
            return job
        except Exception as e:
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(e))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(e)
            return job

        if self.library:
            await self.library.upsert(result)
        return await self._mark_archived(job, result["path"])

    async def _scrape_western(self, job: dict, info: dict) -> dict:
        dest = Path(job.get("dest") or "")
        settle = max(0, int(self.settings.scrape_settle_seconds))
        min_bytes = max(0, int(self.settings.scrape_min_mb) * 1024 * 1024)
        try:
            src, _videos = find_western_videos(
                self.settings.download_dir,
                dest,
                job.get("title") or info.get("title") or "",
                min_bytes,
            )
        except ScrapeError as exc:
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(exc))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(exc)
            return job
        if any(is_incomplete(video) for video in _videos):
            return await self._mark_waiting(job)
        if src.is_dir() and src.resolve() != self.settings.download_dir.resolve() and has_incomplete_files(src):
            return await self._mark_waiting(job)
        mtime = max((video.stat().st_mtime for video in _videos), default=0)
        if mtime and time.time() - mtime < settle:
            return await self._mark_waiting(job)
        try:
            async with self._scrape_lock:
                result = await scrape_western_job(self.settings, job, info)
        except ScrapeError as exc:
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(exc))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(exc)
            return job
        return await self._mark_archived(job, result["path"])

    async def _busy_codes(self) -> set[str]:
        busy: set[str] = set()
        for job in await self.db.list_jobs():
            code = (job.get("code") or "").strip().upper()
            if not code:
                continue
            status = job.get("status")
            if status in ("cancelled", "error"):
                continue
            if status != "complete":
                busy.add(code)
                continue
            scrape_st = job.get("scrape_status") or ""
            if scrape_st in ("waiting", "scraping"):
                busy.add(code)
        return busy

    async def watch_western(self) -> None:
        """Xunlei panel downloads have no jav-dl job. Match Site.YY.MM.DD names."""
        if not self.settings.scrape_enabled or self.settings.western_root is None:
            return
        if not (self.settings.tpdb_api_key or "").strip():
            return
        min_bytes = max(0, int(self.settings.scrape_min_mb) * 1024 * 1024)
        settle = max(0, int(self.settings.scrape_settle_seconds))
        now = time.time()
        ready = await asyncio.to_thread(
            list_ready_western,
            self.settings.download_dir,
            min_bytes,
            settle,
            now,
        )
        for src in ready:
            key = str(src)
            if self._watch_fail.get(key, 0) > now:
                continue
            try:
                async with self._scrape_lock:
                    await scrape_western_source(self.settings, src)
            except ScrapeError:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                continue
            except Exception:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                continue
            self._watch_fail.pop(key, None)
            return

    async def watch_downloads(self) -> None:
        if not self.settings.scrape_enabled:
            return
        min_bytes = max(0, int(self.settings.scrape_min_mb) * 1024 * 1024)
        settle = max(0, int(self.settings.scrape_settle_seconds))
        now = time.time()
        ready = await asyncio.to_thread(
            list_ready_sources,
            self.settings.download_dir,
            min_bytes,
            settle,
            now,
        )
        if not ready:
            return
        busy = await self._busy_codes()
        for code, src in ready:
            if code in busy:
                continue
            key = str(src)
            if self._watch_fail.get(key, 0) > now:
                continue
            try:
                async with self._scrape_lock:
                    result = await scrape_job(
                        self.settings,
                        self.db,
                        {"code": code, "dest": str(src)},
                    )
            except ScrapeError:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                continue
            except Exception:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                continue
            self._watch_fail.pop(key, None)
            if self.library:
                await self.library.upsert(result)
            return

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
            "scrape_status": job.get("scrape_status") or "",
            "scrape_error": job.get("scrape_error"),
            "archive_path": job.get("archive_path") or "",
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

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

from app.codes import normalize_code
from app.config import Settings
from app.db import Database
from app.downloader.aria2 import Aria2, Aria2Error
from app.downloader.xunlei import Xunlei, XunleiError, view_task
from app.scrape import (
    ScrapeError,
    find_code_videos,
    list_ready_sources,
    scrape_job,
    source_incomplete,
    source_mtime,
)
from app.western_archive import (
    find_western_videos,
    list_ready_western,
    read_sidecar,
    release_names_match,
    scrape_western_job,
    scrape_western_source,
)
from app.textutil import format_size
from app.trackers import magnet_for

log = logging.getLogger("app.downloader.jobs")

BackendError = (Aria2Error, XunleiError)
_ARIA2_GID = re.compile(r"[0-9a-fA-F]{16}")

ACTIVE = {"active", "waiting", "paused", "downloading", "queued"}
DONE = {"complete", "error", "removed", "cancelled"}
TERMINAL = {"complete", "cancelled", "error"}
SCRAPE_DONE = {"archived", "skipped"}
CLEARABLE = {
    "complete": ["complete"],
    "cancelled": ["cancelled"],
    "error": ["error"],
    "finished": ["complete", "cancelled", "error"],
}
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
        self._aria2_settled: set[str] = set()

    def _use_xunlei(self) -> bool:
        return (self.settings.downloader or "aria2").strip().lower() == "xunlei"

    def _backend(self, job: dict) -> str:
        name = (job.get("backend") or "").strip().lower()
        if name in ("aria2", "xunlei"):
            return name
        gid = str(job.get("gid") or "").split(",")[0].strip()
        if _ARIA2_GID.fullmatch(gid):
            return "aria2"
        if gid:
            return "xunlei"
        return "xunlei" if self._use_xunlei() else "aria2"

    def _needs_tell(self, job: dict) -> bool:
        if not job.get("gid") or job.get("status") == "cancelled":
            return False
        backend = self._backend(job)
        if backend == "xunlei" and job.get("status") == "complete" and job.get("cleaned"):
            return False
        if backend == "aria2" and job.get("id") in self._aria2_settled:
            return False
        return True

    async def _xunlei_snapshot(self, jobs: list[dict]) -> dict | None:
        if not any(self._needs_tell(job) and self._backend(job) == "xunlei" for job in jobs):
            return None
        try:
            index, truncated = await self.xunlei.list_download_tasks()
        except XunleiError as exc:
            return {"index": {}, "truncated": False, "error": exc}
        return {"index": index, "truncated": truncated, "error": None}

    async def _fetch_status(self, job: dict, snapshot: dict | None) -> tuple[dict, str]:
        gid = str(job.get("gid") or "")
        if self._backend(job) == "xunlei":
            snap = snapshot
            if snap is None:
                index, truncated = await self.xunlei.list_download_tasks()
                snap = {"index": index, "truncated": truncated, "error": None}
            if snap.get("error"):
                raise snap["error"]
            return view_task(gid, snap["index"], bool(snap.get("truncated"))), gid
        resolved = await self.aria2.resolve(gid)
        return resolved, str(resolved.get("stored") or gid)

    async def _add_magnet(self, magnet: str, dest: str) -> str:
        if self._use_xunlei():
            return await self.xunlei.add_magnet(magnet, dest)
        return await self.aria2.add_magnet(magnet, dest)

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
            "backend": "xunlei" if self._use_xunlei() else "aria2",
        }
        await self.db.insert_job(job)
        return await self.public(job)

    async def sync_one(self, job: dict, snapshot: dict | None = None) -> dict:
        if not self._needs_tell(job):
            return job
        backend = self._backend(job)
        try:
            st, stored = await self._fetch_status(job, snapshot)
        except BackendError as exc:
            if job.get("status") in DONE:
                if backend == "aria2":
                    self._aria2_settled.add(job["id"])
                return job
            await self.db.update_job(job["id"], error=str(exc))
            job = dict(job)
            job["error"] = str(exc)
            job["_live"] = {}
            return job
        if st.get("status") == "missing":
            return await self._sync_missing(job, st, backend)

        status = _map_status(st.get("status"), job.get("status") or "queued")
        old_gid = str(job.get("gid") or "")
        fields: dict = {"status": status, "error": None}
        if status == "error":
            fields["error"] = st.get("errorMessage") or job.get("error") or "下载失败"
        gid_changed = bool(stored) and stored != old_gid
        if gid_changed:
            fields["gid"] = stored
            self._aria2_settled.discard(job["id"])
            log.info("aria2 任务 %s 从 %s 跟到 %s", job["id"], old_gid, stored)
        if status == "complete":
            if gid_changed or not job.get("cleaned"):
                await asyncio.to_thread(cleanup_dir, job["dest"])
                fields["cleaned"] = 1
            if backend == "aria2":
                self._aria2_settled.add(job["id"])
        elif gid_changed:
            fields["cleaned"] = 0
        await self.db.update_job(job["id"], **fields)
        job = dict(job)
        job.update(fields)
        job["_live"] = st
        return job

    async def _sync_missing(self, job: dict, status: dict, backend: str) -> dict:
        if status.get("truncated"):
            return job
        if job.get("status") == "complete":
            fields: dict = {}
            if not job.get("cleaned"):
                await asyncio.to_thread(cleanup_dir, job["dest"])
                fields["cleaned"] = 1
            if fields:
                await self.db.update_job(job["id"], **fields)
                job = dict(job)
                job.update(fields)
            if backend == "aria2":
                self._aria2_settled.add(job["id"])
            return job
        if job.get("status") != "error":
            log.warning(
                "任务在下载器里找不到 id=%s gid=%s：%s",
                job["id"],
                job.get("gid"),
                status.get("errorMessage") or "",
            )
        fields = {
            "status": "error",
            "error": status.get("errorMessage") or "任务不存在或已删",
        }
        await self.db.update_job(job["id"], **fields)
        job = dict(job)
        job.update(fields)
        job["_live"] = {}
        return job

    async def sync_all(self) -> None:
        jobs = await self.db.list_jobs()
        snapshot = await self._xunlei_snapshot(jobs)
        for job in jobs:
            try:
                if job.get("status") == "cancelled":
                    continue
                if self._needs_tell(job):
                    job = await self.sync_one(job, snapshot)
                scrape_st = job.get("scrape_status") or ""
                if (
                    job.get("status") == "complete"
                    and job.get("cleaned")
                    and scrape_st not in SCRAPE_DONE
                ):
                    await self.maybe_scrape(job)
            except Exception:
                log.warning("同步任务失败 id=%s", job.get("id"), exc_info=True)
                continue
        now = time.time()
        if now - self._last_watch >= WATCH_EVERY:
            self._last_watch = now
            try:
                await self.watch_western()
            except Exception:
                log.warning("欧美监控失败", exc_info=True)
            try:
                await self.watch_downloads()
            except Exception:
                log.warning("下载目录监控失败", exc_info=True)

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
            found = await asyncio.to_thread(
                find_code_videos,
                job.get("code") or "",
                dest,
                self.settings.download_dir,
                min_bytes,
            )
        except ScrapeError:
            if hit and hit.get("has_video"):
                return await self._mark_archived(job, hit.get("path") or "")
            log.warning("刮削失败 %s: 没有可归档的视频", job.get("code"))
            await self.db.update_job(
                job["id"],
                scrape_status="error",
                scrape_error="没有可归档的视频",
            )
            job["scrape_status"] = "error"
            job["scrape_error"] = "没有可归档的视频"
            return job

        src, _videos = found
        if await asyncio.to_thread(source_incomplete, src):
            return await self._mark_waiting(job)
        mtime = await asyncio.to_thread(source_mtime, src)
        if mtime and time.time() - mtime < settle:
            return await self._mark_waiting(job)

        job = await self._mark_scraping(job)
        try:
            async with self._scrape_lock:
                result = await scrape_job(self.settings, self.db, job, found=found)
        except ScrapeError as e:
            log.warning("刮削失败 %s: %s", job.get("code"), e)
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(e))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(e)
            return job
        except Exception as e:
            log.warning("刮削失败 %s", job.get("code"), exc_info=True)
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
            found = await asyncio.to_thread(
                find_western_videos,
                self.settings.download_dir,
                dest,
                job.get("title") or info.get("title") or "",
                min_bytes,
            )
        except ScrapeError as exc:
            log.warning("刮削失败 %s: %s", job.get("code"), exc)
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(exc))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(exc)
            return job
        src, _videos = found
        if await asyncio.to_thread(source_incomplete, src):
            return await self._mark_waiting(job)
        mtime = await asyncio.to_thread(source_mtime, src)
        if mtime and time.time() - mtime < settle:
            return await self._mark_waiting(job)
        job = await self._mark_scraping(job)
        try:
            async with self._scrape_lock:
                result = await scrape_western_job(self.settings, job, info, found=found)
        except ScrapeError as exc:
            log.warning("刮削失败 %s: %s", job.get("code"), exc)
            await self.db.update_job(job["id"], scrape_status="error", scrape_error=str(exc))
            job["scrape_status"] = "error"
            job["scrape_error"] = str(exc)
            return job
        if self.library:
            await self.library.remember_western(result)
        return await self._mark_archived(job, result["path"])

    def _owns_files(self, job: dict) -> bool:
        if job.get("status") in ("cancelled", "error"):
            return False
        return (job.get("scrape_status") or "") not in SCRAPE_DONE

    async def _owning_jobs(self) -> list[dict]:
        return [job for job in await self.db.list_jobs() if self._owns_files(job)]

    def _claims(self, job: dict, src: Path) -> bool:
        dest = Path(job.get("dest") or "")
        if dest.parts and _paths_overlap(dest, src):
            return True
        if normalize_code(job.get("code") or ""):
            return False
        label = src.stem if src.is_file() else src.name
        return release_names_match(label, job.get("title") or "")

    async def _busy_codes(self) -> set[str]:
        busy: set[str] = set()
        for job in await self._owning_jobs():
            code = normalize_code(job.get("code") or "")
            if code:
                busy.add(code)
        return busy

    async def _mark_scraping(self, job: dict) -> dict:
        await self.db.update_job(job["id"], scrape_status="scraping", scrape_error=None)
        job["scrape_status"] = "scraping"
        job["scrape_error"] = None
        return job

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
        owners = await self._owning_jobs()
        for src in ready:
            if any(self._claims(job, src) for job in owners):
                continue
            key = str(src)
            if self._watch_fail.get(key, 0) > now:
                continue
            try:
                async with self._scrape_lock:
                    result = await scrape_western_source(self.settings, src)
            except ScrapeError as exc:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                log.warning("监控归档失败 %s: %s", key, exc)
                continue
            except Exception:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                log.warning("监控归档失败 %s", key, exc_info=True)
                continue
            self._watch_fail.pop(key, None)
            if self.library and isinstance(result, dict):
                await self.library.remember_western(result)
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
            except ScrapeError as exc:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                log.warning("监控归档失败 %s: %s", key, exc)
                continue
            except Exception:
                self._watch_fail[key] = now + SCRAPE_RETRY_AFTER
                log.warning("监控归档失败 %s", key, exc_info=True)
                continue
            self._watch_fail.pop(key, None)
            if self.library:
                await self.library.upsert(result)
            return

    def _row(
        self,
        job: dict,
        *,
        status: str,
        error: str | None,
        progress: float,
        downloaded: str,
        total: str,
        speed: str,
        eta: str,
        connections: int,
        seeders: int,
    ) -> dict:
        return {
            "id": job["id"],
            "code": job["code"],
            "info_hash": job["info_hash"],
            "title": job["title"],
            "status": status,
            "dest": job["dest"],
            "error": error,
            "progress": progress,
            "downloaded": downloaded,
            "total": total,
            "speed": speed,
            "eta": eta,
            "connections": connections,
            "seeders": seeders,
            "created_at": job.get("created_at"),
            "scrape_status": job.get("scrape_status") or "",
            "scrape_error": job.get("scrape_error"),
            "archive_path": job.get("archive_path") or "",
        }

    async def public(self, job: dict, snapshot: dict | None = None) -> dict:
        live = job.get("_live")
        if live is None and self._needs_tell(job):
            try:
                live, _stored = await self._fetch_status(job, snapshot)
            except BackendError:
                live = {}
        if (live or {}).get("status") == "missing":
            live = {}
        live = live or {}
        if not live and job.get("status") == "complete":
            return self._row(
                job,
                status="complete",
                error=None,
                progress=100.0,
                downloaded="—",
                total="—",
                speed="—",
                eta="",
                connections=0,
                seeders=0,
            )
        total = int(live.get("totalLength") or 0)
        done = int(live.get("completedLength") or 0)
        speed = int(live.get("downloadSpeed") or 0)
        pct = (done / total * 100) if total else 0.0
        status = _map_status(live.get("status"), job.get("status") or "queued")
        return self._row(
            job,
            status=status,
            error=live.get("errorMessage") or job.get("error"),
            progress=round(pct, 1),
            downloaded=format_size(done) if done else "0 B",
            total=format_size(total) if total else "?",
            speed=f"{format_size(speed)}/s" if speed else "0 B/s",
            eta=_eta(total, done, speed),
            connections=int(live.get("connections") or 0),
            seeders=int(live.get("numSeeders") or 0),
        )

    async def list_public(self) -> list[dict]:
        jobs = await self.db.list_jobs()
        snapshot = await self._xunlei_snapshot(jobs)
        out = []
        for job in jobs:
            try:
                job = await self.sync_one(job, snapshot)
            except Exception:
                log.warning("同步任务失败 id=%s", job.get("id"), exc_info=True)
            out.append(await self.public(job, snapshot))
        return out

    async def _control(self, job: dict, op: str) -> None:
        if self._backend(job) == "xunlei":
            action = {"pause": self.xunlei.pause, "resume": self.xunlei.resume, "remove": self.xunlei.remove}[op]
            await action(str(job.get("gid") or ""))
            return
        if not job.get("gid"):
            return
        resolved = await self.aria2.resolve(str(job["gid"]))
        if resolved.get("status") == "missing":
            if op == "remove":
                return
            raise Aria2Error("aria2 里没有这个任务")
        stored = str(resolved.get("stored") or job.get("gid"))
        if stored != job.get("gid"):
            await self.db.update_job(job["id"], gid=stored, cleaned=0)
            job["gid"] = stored
            log.info("aria2 任务 %s 从元数据跟到 %s", job["id"], stored)
        for gid in resolved.get("gids") or []:
            if op == "pause":
                await self.aria2.pause(gid)
            elif op == "resume":
                await self.aria2.resume(gid)
            else:
                await self.aria2.remove(gid)

    async def pause(self, job_id: str) -> dict:
        job = await self._require(job_id)
        await self._control(job, "pause")
        await self.db.update_job(job_id, status="paused")
        job["status"] = "paused"
        return await self.public(job)

    async def resume(self, job_id: str) -> dict:
        job = await self._require(job_id)
        await self._control(job, "resume")
        await self.db.update_job(job_id, status="downloading")
        job["status"] = "downloading"
        return await self.public(job)

    async def cancel(self, job_id: str) -> dict:
        job = await self._require(job_id)
        await self._control(job, "remove")
        await self.db.update_job(job_id, status="cancelled")
        job["status"] = "cancelled"
        self._aria2_settled.add(job_id)
        return await self.public(job)

    async def delete(self, job_id: str) -> None:
        job = await self._require(job_id)
        if job.get("status") not in TERMINAL:
            raise ValueError("进行中的任务请先取消")
        await self.db.delete_job(job_id)
        self._aria2_settled.discard(job_id)

    async def clear_finished(self, status: str) -> int:
        statuses = CLEARABLE.get((status or "").strip().lower())
        if not statuses:
            raise ValueError("只能清理已完成、已取消或失败的记录")
        deleted = await self.db.delete_jobs_with_status(statuses)
        if "complete" in statuses:
            self._aria2_settled.clear()
        return deleted

    async def rescrape(self, job_id: str) -> dict:
        job = await self._require(job_id)
        if job.get("status") != "complete":
            raise ValueError("只有下载完成的任务可以重新刮削")
        await self.db.update_job(job_id, scrape_status="", scrape_error=None, archive_path="")
        fresh = await self.db.get_job(job_id)
        if not fresh:
            raise KeyError(job_id)
        return await self.public(await self.maybe_scrape(fresh))

    async def _require(self, job_id: str) -> dict:
        job = await self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        return job


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        a = left.resolve() if left.exists() else left
        b = right.resolve() if right.exists() else right
    except OSError:
        return False
    if a == b:
        return True
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False

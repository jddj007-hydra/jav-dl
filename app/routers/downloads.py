from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.codes import normalize_code
from app.downloader.jobs import BackendError
from app.models import ClearDownloads, DownloadRequest
from app.slug import western_slug
from app.western_archive import write_sidecar

router = APIRouter()


@router.get("/api/downloads")
async def list_downloads(request: Request):
    jobs = request.app.state.jobs
    return {"items": await jobs.list_public()}


@router.post("/api/downloads")
async def create_download(request: Request, body: DownloadRequest):
    info_hash = (body.info_hash or "").strip().lower()
    if len(info_hash) != 40:
        raise HTTPException(400, "info_hash 无效")
    kind = (body.kind or "").strip().lower()
    try:
        if kind == "western":
            work = (body.work_title or body.title or "").strip()
            if not work:
                raise HTTPException(400, "缺少作品标题")
            slug = western_slug(body.site, body.date, work, body.tpdb_id)
            job = await request.app.state.jobs.enqueue(
                slug,
                info_hash,
                body.title or work,
                dest_rel=f"western/{slug}",
            )
            kind_name = (body.tpdb_kind or "scene").strip().lower()
            if kind_name not in ("scene", "movie"):
                kind_name = "scene"
            write_sidecar(Path(job["dest"]), {
                "kind": "western",
                "tpdb_id": (body.tpdb_id or "").strip(),
                "tpdb_kind": kind_name,
                "site": (body.site or "").strip(),
                "title": work,
                "date": (body.date or "").strip(),
                "performers": [name.strip() for name in body.performers if name and name.strip()],
            })
        else:
            code = normalize_code(body.code)
            if not code:
                raise HTTPException(400, "番号格式无效")
            job = await request.app.state.jobs.enqueue(code, info_hash, body.title)
    except BackendError as e:
        raise HTTPException(503, str(e)) from e
    return job


@router.post("/api/downloads/{job_id}/pause")
async def pause(request: Request, job_id: str):
    return await _act(request, job_id, "pause")


@router.post("/api/downloads/{job_id}/resume")
async def resume(request: Request, job_id: str):
    return await _act(request, job_id, "resume")


@router.post("/api/downloads/{job_id}/cancel")
async def cancel(request: Request, job_id: str):
    return await _act(request, job_id, "cancel")


@router.post("/api/downloads/{job_id}/rescrape")
async def rescrape(request: Request, job_id: str):
    jobs = request.app.state.jobs
    try:
        return await jobs.rescrape(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/downloads/{job_id}")
async def delete_download(request: Request, job_id: str):
    jobs = request.app.state.jobs
    try:
        await jobs.delete(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/api/downloads/clear")
async def clear_downloads(request: Request, body: ClearDownloads):
    jobs = request.app.state.jobs
    try:
        deleted = await jobs.clear_finished(body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": deleted}


@router.post("/api/library/refresh")
async def refresh_library(request: Request):
    library = getattr(request.app.state, "library", None)
    if library is None:
        raise HTTPException(503, "媒体库还没准备好")
    return {"count": await library.refresh()}


async def _act(request: Request, job_id: str, op: str):
    jobs = request.app.state.jobs
    try:
        fn = getattr(jobs, op)
        return await fn(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except BackendError as e:
        raise HTTPException(503, str(e)) from e

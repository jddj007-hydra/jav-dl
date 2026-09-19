from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.codes import normalize_code
from app.downloader.jobs import BackendError
from app.models import DownloadRequest

router = APIRouter()


@router.get("/api/downloads")
async def list_downloads(request: Request):
    jobs = request.app.state.jobs
    return {"items": await jobs.list_public()}


@router.post("/api/downloads")
async def create_download(request: Request, body: DownloadRequest):
    code = normalize_code(body.code)
    if not code:
        raise HTTPException(400, "番号格式无效")
    info_hash = (body.info_hash or "").strip().lower()
    if len(info_hash) != 40:
        raise HTTPException(400, "info_hash 无效")
    try:
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


async def _act(request: Request, job_id: str, op: str):
    jobs = request.app.state.jobs
    try:
        fn = getattr(jobs, op)
        return await fn(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except BackendError as e:
        raise HTTPException(503, str(e)) from e

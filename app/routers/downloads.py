from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.batch import enqueue_batch, preview_batch
from app.codes import normalize_code
from app.downloader.jobs import BackendError
from app.models import BatchEnqueue, BatchText, ClearDownloads, DownloadRequest
from app.slug import western_slug
from app.western_archive import write_sidecar

router = APIRouter()


@router.get("/api/downloads")
async def list_downloads(request: Request):
    jobs = request.app.state.jobs
    unread = 0
    db = getattr(request.app.state, "db", None)
    if db is not None and hasattr(db, "count_unread_hits"):
        unread = await db.count_unread_hits()
    return {"items": await jobs.list_public(), "follow_unread": unread}


def _target(body: DownloadRequest) -> tuple[str, str | None, str]:
    kind = (body.kind or "").strip().lower()
    if kind == "western":
        work = (body.work_title or body.title or "").strip()
        if not work:
            raise HTTPException(400, "缺少作品标题")
        slug = western_slug(body.site, body.date, work, body.tpdb_id)
        return slug, f"western/{slug}", body.title or work
    code = normalize_code(body.code)
    if not code:
        raise HTTPException(400, "番号格式无效")
    return code, None, body.title


def _remember_western(body: DownloadRequest, dest: str) -> None:
    if (body.kind or "").strip().lower() != "western":
        return
    kind_name = (body.tpdb_kind or "scene").strip().lower()
    if kind_name not in ("scene", "movie"):
        kind_name = "scene"
    work = (body.work_title or body.title or "").strip()
    write_sidecar(Path(dest), {
        "kind": "western",
        "tpdb_id": (body.tpdb_id or "").strip(),
        "tpdb_kind": kind_name,
        "site": (body.site or "").strip(),
        "title": work,
        "date": (body.date or "").strip(),
        "performers": [name.strip() for name in body.performers if name and name.strip()],
    })


def _hash(body: DownloadRequest) -> str:
    info_hash = (body.info_hash or "").strip().lower()
    if len(info_hash) != 40 or any(ch not in "0123456789abcdef" for ch in info_hash):
        raise HTTPException(400, "info_hash 无效")
    return info_hash


@router.post("/api/downloads")
async def create_download(request: Request, body: DownloadRequest):
    info_hash = _hash(body)
    jobs = request.app.state.jobs
    if (body.pick_token or "").strip():
        try:
            job = await jobs.confirm_files(body.pick_token.strip(), body.file_indexes or [], info_hash)
        except KeyError:
            raise HTTPException(404, "文件选择已过期，请重新点下载") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except BackendError as exc:
            raise HTTPException(503, str(exc)) from exc
        _remember_western(body, job["dest"])
        return job
    code, dest_rel, title = _target(body)
    try:
        job = await jobs.enqueue(code, info_hash, title, dest_rel=dest_rel)
    except BackendError as e:
        raise HTTPException(503, str(e)) from e
    _remember_western(body, job["dest"])
    return job


@router.post("/api/downloads/files")
async def preview_files(request: Request, body: DownloadRequest):
    info_hash = _hash(body)
    code, dest_rel, title = _target(body)
    try:
        result = await request.app.state.jobs.prepare_files(
            code, info_hash, title, dest_rel=dest_rel
        )
    except BackendError as exc:
        raise HTTPException(503, str(exc)) from exc
    if result.get("mode") == "direct":
        _remember_western(body, result["job"]["dest"])
    return result


@router.delete("/api/downloads/files/{token}")
async def cancel_files(request: Request, token: str):
    await request.app.state.jobs.cancel_files(token)
    return {"ok": True}


@router.post("/api/downloads/batch/preview")
async def batch_preview(request: Request, body: BatchText):
    try:
        items = await preview_batch(request.app.state.settings, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"items": items}


@router.post("/api/downloads/batch")
async def batch_enqueue(request: Request, body: BatchEnqueue):
    rows = [item.model_dump() for item in body.items]
    try:
        return await enqueue_batch(request.app.state.jobs, rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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

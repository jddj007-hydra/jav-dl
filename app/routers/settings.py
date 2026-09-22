from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import save_user_config
from app.downloader.aria2 import Aria2
from app.downloader.xunlei import Xunlei
from app.models import SettingsUpdate

router = APIRouter()


def bind_runtime(app, settings) -> None:
    app.state.settings = settings
    app.state.aria2 = Aria2(settings)
    app.state.xunlei = Xunlei(settings)
    jobs = app.state.jobs
    jobs.settings = settings
    jobs.aria2 = app.state.aria2
    jobs.xunlei = app.state.xunlei
    library = getattr(app.state, "library", None)
    if library is not None:
        library.settings = settings


@router.get("/api/settings")
async def get_settings(request: Request):
    return request.app.state.settings.public_dict()


@router.put("/api/settings")
async def put_settings(request: Request, body: SettingsUpdate):
    updates = body.model_dump(exclude_none=True)
    if updates.get("xunlei_password") == "":
        updates.pop("xunlei_password")
    if updates.get("tpdb_api_key") == "":
        updates.pop("tpdb_api_key")
    settings = save_user_config(request.app.state.settings, updates)
    bind_runtime(request.app, settings)
    library = getattr(request.app.state, "library", None)
    if library is not None:
        await library.refresh()
    return settings.public_dict()

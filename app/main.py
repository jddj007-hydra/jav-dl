from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import load_settings
from app.db import Database
from app.downloader.aria2 import Aria2
from app.downloader.jobs import JobManager
from app.downloader.xunlei import Xunlei
from app.routers import downloads, health, images, resources, search, settings as settings_router

STATIC = Path(__file__).parent / "static"


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        user, pw = settings.auth_user, settings.auth_pass
        if not (user and pw):
            return await call_next(request)
        if request.url.path == "/api/health":
            return await call_next(request)
        import base64

        header = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        if not header or not secrets.compare_digest(header, expected):
            headers = {"WWW-Authenticate": 'Basic realm="jav-dl"'}
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "需要登录"},
                    status_code=401,
                    headers=headers,
                )
            return PlainTextResponse(
                "需要登录",
                status_code=401,
                headers=headers,
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db = Database(settings)
    await db.init()
    aria2 = Aria2(settings)
    xunlei = Xunlei(settings)
    jobs = JobManager(settings, db, aria2, xunlei)
    app.state.settings = settings
    app.state.db = db
    app.state.aria2 = aria2
    app.state.xunlei = xunlei
    app.state.jobs = jobs

    stop = asyncio.Event()

    async def poll():
        while not stop.is_set():
            try:
                await jobs.sync_all()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(poll())
    yield
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(title="jav-dl", lifespan=lifespan)
    app.add_middleware(BasicAuthMiddleware)
    app.include_router(health.router)
    app.include_router(search.router)
    app.include_router(resources.router)
    app.include_router(downloads.router)
    app.include_router(settings_router.router)
    app.include_router(images.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()

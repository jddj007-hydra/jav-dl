from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.httputil import site_client

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    settings = request.app.state.settings
    aria2_ver = await request.app.state.aria2.version()
    xunlei_ver = await request.app.state.xunlei.version()
    javbus_ok = False
    clm_ok = False
    async with site_client(settings) as client:
        try:
            r = await client.get(settings.javbus_base.rstrip("/") + "/", timeout=8.0)
            javbus_ok = r.status_code < 500
        except Exception:
            javbus_ok = False
        try:
            r = await client.get(settings.clm_search.rstrip("/") + "/", timeout=8.0)
            clm_ok = r.status_code < 500
        except Exception:
            clm_ok = False
    return {
        "ok": True,
        "version": __version__,
        "downloader": settings.downloader,
        "aria2": {"ok": bool(aria2_ver), "version": aria2_ver},
        "xunlei": {"ok": bool(xunlei_ver), "version": xunlei_ver},
        "javbus": {"ok": javbus_ok, "base": settings.javbus_base},
        "clm": {"ok": clm_ok, "base": settings.clm_search},
        "proxy_enabled": settings.proxy_enabled,
    }

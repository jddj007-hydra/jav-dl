from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app import __version__
from app.httputil import site_client

router = APIRouter()
PROBE_TIMEOUT = 3.0


async def _bounded(coro, default):
    try:
        return await asyncio.wait_for(coro, PROBE_TIMEOUT)
    except Exception:
        return default


async def _site_ok(settings, url: str) -> bool:
    try:
        async with site_client(settings) as client:
            response = await asyncio.wait_for(
                client.get(url, timeout=PROBE_TIMEOUT),
                PROBE_TIMEOUT,
            )
            return response.status_code < 500
    except Exception:
        return False


@router.get("/api/health")
async def health(request: Request):
    settings = request.app.state.settings
    aria2_ver, xunlei_ver, javbus_ok, clm_ok = await asyncio.gather(
        _bounded(request.app.state.aria2.version(), None),
        _bounded(request.app.state.xunlei.version(), None),
        _site_ok(settings, settings.javbus_base.rstrip("/") + "/"),
        _site_ok(settings, settings.clm_search.rstrip("/") + "/"),
    )
    return {
        "ok": True,
        "version": __version__,
        "downloader": settings.downloader,
        "aria2": {"ok": bool(aria2_ver), "version": aria2_ver},
        "xunlei": {"ok": bool(xunlei_ver), "version": xunlei_ver},
        "javbus": {"ok": javbus_ok, "base": settings.javbus_base},
        "clm": {"ok": clm_ok, "base": settings.clm_search},
        "tpdb": {"ok": bool(settings.tpdb_api_key), "configured": bool(settings.tpdb_api_key)},
        "proxy_enabled": settings.proxy_enabled,
    }

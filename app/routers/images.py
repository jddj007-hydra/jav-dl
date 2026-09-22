from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.httputil import site_client

router = APIRouter()

ALLOWED_HOST_PARTS = (
    "javbus.com",
    "javbus.org",
    "seejav.",
    "cdnbus.",
    "pics.dmm.co.jp",
    "pics.dmm.com",
    "awsimgsrc.dmm.co.jp",
)
TPDB_HOSTS = ("theporndb.net", "metadataapi.net")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_tpdb(host: str) -> bool:
    return any(host == name or host.endswith("." + name) for name in TPDB_HOSTS)


def _allowed(url: str, javbus_base: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if _is_tpdb(host):
        return True
    base_host = urlparse(javbus_base).hostname or ""
    if host == base_host.lower():
        return True
    return any(part in host for part in ALLOWED_HOST_PARTS)


@router.get("/api/img")
async def proxy_img(request: Request, url: str = Query(...)):
    settings = request.app.state.settings
    if url.startswith("/"):
        url = settings.javbus_base.rstrip("/") + url
    if not _allowed(url, settings.javbus_base):
        raise HTTPException(400, "不允许的图片地址")
    cache_dir: Path = settings.data_dir / "img_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    cached = cache_dir / key
    meta = cache_dir / f"{key}.ct"
    if cached.exists() and cached.stat().st_size > 0:
        media = meta.read_text(encoding="utf-8") if meta.exists() else "image/jpeg"
        return FileResponse(cached, media_type=media)
    referer = (
        "https://theporndb.net/"
        if _is_tpdb(_host(url))
        else settings.javbus_base.rstrip("/") + "/"
    )
    async with site_client(settings) as client:
        r = await client.get(url, headers={"Referer": referer})
    if r.status_code >= 400 or not r.content:
        raise HTTPException(502, "封面下载失败")
    if len(r.content) > 8 * 1024 * 1024:
        raise HTTPException(502, "封面过大")
    media = r.headers.get("content-type", "image/jpeg").split(";")[0]
    cached.write_bytes(r.content)
    meta.write_text(media, encoding="utf-8")
    return Response(content=r.content, media_type=media)

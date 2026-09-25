from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.httputil import image_client
from app.imgcache import trim_img_cache

router = APIRouter()

ALLOWED_SUFFIXES = (
    "javbus.com",
    "javbus.org",
    "dmm.co.jp",
    "dmm.com",
    "theporndb.net",
    "metadataapi.net",
)
MIRROR_NAMES = {"seejav", "cdnbus"}
MAX_REDIRECTS = 3


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def host_is(host: str, suffix: str) -> bool:
    host = (host or "").lower().rstrip(".")
    suffix = (suffix or "").lower().rstrip(".")
    if not host or not suffix:
        return False
    return host == suffix or host.endswith("." + suffix)


def _mirror_name(host: str) -> bool:
    labels = [part for part in host.split(".") if part]
    return len(labels) >= 2 and labels[-2] in MIRROR_NAMES


def _is_tpdb(host: str) -> bool:
    return any(host_is(host, name) for name in ("theporndb.net", "metadataapi.net"))


def _allowed(url: str, javbus_base: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False
    host = _host(url)
    if not host:
        return False
    base_host = _host(javbus_base)
    if base_host and host_is(host, base_host):
        return True
    if any(host_is(host, suffix) for suffix in ALLOWED_SUFFIXES):
        return True
    return _mirror_name(host)


def image_media_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def fetch_image(settings, url: str, referer: str, javbus_base: str) -> tuple[bytes, str]:
    client = image_client(settings)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed(current, javbus_base):
            raise HTTPException(400, "不允许的图片地址")
        response = await client.get(current, headers={"Referer": referer})
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location") or ""
            if not location:
                break
            current = urljoin(current, location)
            continue
        if response.status_code >= 400 or not response.content:
            raise HTTPException(502, "封面下载失败")
        if len(response.content) > 8 * 1024 * 1024:
            raise HTTPException(502, "封面过大")
        media = image_media_type(response.content)
        if not media:
            raise HTTPException(502, "封面不是图片")
        return response.content, media
    raise HTTPException(502, "封面跳转过多")


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
        if media not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            media = "image/jpeg"
        return FileResponse(cached, media_type=media)
    referer = (
        "https://theporndb.net/"
        if _is_tpdb(_host(url))
        else settings.javbus_base.rstrip("/") + "/"
    )
    content, media = await fetch_image(settings, url, referer, settings.javbus_base)
    cached.write_bytes(content)
    meta.write_text(media, encoding="utf-8")
    trim_img_cache(cache_dir)
    return Response(content=content, media_type=media)

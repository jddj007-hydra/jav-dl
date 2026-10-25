from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.config import site_origin
from app.httputil import describe_http_error, image_client
from app.imgcache import trim_img_cache

router = APIRouter()

ALLOWED_SUFFIXES = (
    "javbus.com",
    "javbus.org",
    "dmm.co.jp",
    "dmm.com",
    "mgstage.com",
    "theporndb.net",
    "metadataapi.net",
    "fc2.com",
    "javdb.com",
    "jdbstatic.com",
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


def _allowed(url: str, javbus_base: str, *extra_bases: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False
    host = _host(url)
    if not host:
        return False
    for base in (javbus_base, *extra_bases):
        base_host = _host(base)
        if base_host and host_is(host, base_host):
            return True
    if any(host_is(host, suffix) for suffix in ALLOWED_SUFFIXES):
        return True
    return _mirror_name(host)


def image_referer(settings, url: str) -> str:
    """JavDB 图床用 JavDB 地址做 Referer，其余保持原来的站点。"""
    host = _host(url)
    if _is_tpdb(host):
        return "https://theporndb.net/"
    javdb = (getattr(settings, "javdb_base", "") or "").strip()
    javdb_host = _host(javdb) if javdb else ""
    if host and (
        host_is(host, "javdb.com")
        or host_is(host, "jdbstatic.com")
        or (javdb_host and host_is(host, javdb_host))
    ):
        origin = site_origin(javdb) if javdb else ""
        return (origin or "https://javdb.com") + "/"
    return (getattr(settings, "javbus_base", "") or "").rstrip("/") + "/"


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


async def fetch_image(settings, url: str, referer: str, javbus_base: str, *extra_bases: str) -> tuple[bytes, str]:
    client = image_client(settings)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed(current, javbus_base, *extra_bases):
            raise HTTPException(400, "不允许的图片地址")
        try:
            response = await client.get(current, headers={"Referer": referer})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"封面下载失败: {describe_http_error(exc)}") from exc
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
    javdb_base = (getattr(settings, "javdb_base", "") or "").strip()
    extra = (javdb_base,) if javdb_base else ()
    if not _allowed(url, settings.javbus_base, *extra):
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
    referer = image_referer(settings, url)
    content, media = await fetch_image(settings, url, referer, settings.javbus_base, *extra)
    cached.write_bytes(content)
    meta.write_text(media, encoding="utf-8")
    trim_img_cache(cache_dir)
    return Response(content=content, media_type=media)

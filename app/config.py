from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

USER_KEYS = (
    "proxy_enabled",
    "proxy_url",
    "javbus_base",
    "clm_home",
    "clm_search",
    "clm_search_backup",
    "downloader",
    "xunlei_url",
    "xunlei_username",
    "xunlei_password",
    "xunlei_device_name",
    "scrape_enabled",
    "media_dir",
    "western_media_dir",
    "scrape_settle_seconds",
    "scrape_min_mb",
    "tpdb_api_key",
    "notify_channel",
    "notify_telegram_token",
    "notify_telegram_chat",
    "notify_bark_url",
    "notify_serverchan_key",
)

DOWNLOADERS = ("aria2", "xunlei")


def normalize_downloader(value: str | None) -> str:
    v = (value or "aria2").strip().lower()
    return v if v in DOWNLOADERS else "aria2"


def browser_http_url(raw: str | None) -> str | None:
    """Keep an http(s) URL a browser can open. Drop userinfo so a link cannot leak a password."""
    text = (raw or "").strip()
    if not text or any(ch in text for ch in " \t\r\n\"'<>"):
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return text.rstrip("/") or None


def site_origin(raw: str | None) -> str | None:
    """http(s) origin only. Drop path, query, and userinfo."""
    text = browser_http_url(raw)
    if not text:
        return None
    parsed = urlparse(text)
    if not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def panel_links(settings: Settings) -> list[dict]:
    """Browser jump targets for downloaders that actually have a web page.

    迅雷面板就是 xunlei_url。aria2 只提供 JSON-RPC，默认还绑在 127.0.0.1，
    浏览器打不开，所以这里不给 aria2 链接。
    """
    links: list[dict] = []
    downloader = normalize_downloader(settings.downloader)
    xunlei_configured = (
        downloader == "xunlei"
        or bool((settings.xunlei_username or "").strip())
        or bool(settings.xunlei_password)
    )
    xunlei_url = browser_http_url(settings.xunlei_url)
    if xunlei_configured and xunlei_url:
        links.append({"id": "xunlei", "label": "迅雷面板", "url": xunlei_url})
    return links


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8787
    data_dir: Path = Path("./data")
    download_dir: Path = Path("./downloads")
    media_dir: Path = Path("./media")
    western_media_dir: str = ""
    scrape_enabled: bool = True
    scrape_settle_seconds: int = 60
    scrape_min_mb: int = 50
    aria2_rpc: str = "http://127.0.0.1:6800/jsonrpc"
    aria2_secret: str = "jav-dl-rpc"
    downloader: str = "aria2"
    xunlei_url: str = "http://127.0.0.1:2345"
    xunlei_username: str = ""
    xunlei_password: str = ""
    xunlei_device_name: str = "群晖-xunlei"
    proxy_enabled: bool = False
    proxy_url: str = "http://127.0.0.1:7890"
    javbus_base: str = "https://www.javbus.com"
    clm_home: str = "https://clm.cc"
    clm_search: str = "https://clm64.top"
    clm_search_backup: str = ""
    auth_user: str = ""
    auth_pass: str = ""
    metadata_ttl: int = 86400
    latest_ttl: int = 7200
    tpdb_api_key: str = ""
    notify_channel: str = ""
    notify_telegram_token: str = ""
    notify_telegram_chat: str = ""
    notify_bark_url: str = ""
    notify_serverchan_key: str = ""
    http_timeout: float = 20.0
    verify_tls: bool = False

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "img_cache").mkdir(parents=True, exist_ok=True)
        try:
            self.media_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @property
    def western_root(self) -> Path | None:
        raw = (self.western_media_dir or "").strip()
        if not raw or raw == ".":
            return None
        return Path(raw)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jav-dl.db"

    @property
    def user_config_path(self) -> Path:
        return self.data_dir / "config.json"

    def public_dict(self) -> dict:
        return {
            "proxy_enabled": self.proxy_enabled,
            "proxy_url": self.proxy_url,
            "javbus_base": self.javbus_base,
            "clm_home": self.clm_home,
            "clm_search": self.clm_search,
            "clm_search_backup": self.clm_search_backup or "",
            "download_dir": str(self.download_dir),
            "aria2_rpc": self.aria2_rpc,
            "downloader": normalize_downloader(self.downloader),
            "xunlei_url": self.xunlei_url,
            "xunlei_username": self.xunlei_username,
            "xunlei_password_set": bool(self.xunlei_password),
            "xunlei_device_name": self.xunlei_device_name,
            "scrape_enabled": bool(self.scrape_enabled),
            "media_dir": str(self.media_dir),
            "western_media_dir": self.western_media_dir or "",
            "scrape_settle_seconds": int(self.scrape_settle_seconds),
            "scrape_min_mb": int(self.scrape_min_mb),
            "auth_enabled": bool(self.auth_user and self.auth_pass),
            "verify_tls": bool(self.verify_tls),
            "tpdb_api_key_set": bool(self.tpdb_api_key),
            "notify_channel": self.notify_channel or "",
            "notify_telegram_chat": self.notify_telegram_chat or "",
            "notify_telegram_token_set": bool(self.notify_telegram_token),
            "notify_bark_set": bool(self.notify_bark_url),
            "notify_serverchan_set": bool(self.notify_serverchan_key),
            "panels": panel_links(self),
        }


def _keep_paths(allowed: dict) -> None:
    if "media_dir" in allowed:
        raw = str(allowed["media_dir"]).strip()
        if not raw:
            allowed.pop("media_dir")
        else:
            allowed["media_dir"] = Path(raw)
    if "western_media_dir" in allowed:
        raw = str(allowed["western_media_dir"]).strip()
        if not raw:
            allowed.pop("western_media_dir")
        else:
            allowed["western_media_dir"] = raw
    # 备用域留空表示关掉。非法地址不覆盖原来的值。
    if "clm_search_backup" in allowed:
        raw = str(allowed["clm_search_backup"]).strip()
        if not raw:
            allowed["clm_search_backup"] = ""
        else:
            origin = site_origin(raw)
            if not origin:
                allowed.pop("clm_search_backup")
            else:
                allowed["clm_search_backup"] = origin
    for key in ("scrape_settle_seconds", "scrape_min_mb"):
        if key not in allowed:
            continue
        try:
            number = int(allowed[key])
        except (TypeError, ValueError):
            allowed.pop(key)
            continue
        if number < 0:
            allowed.pop(key)
        else:
            allowed[key] = number
    if "notify_channel" in allowed:
        from app.notify import normalize_channel

        channel = normalize_channel(str(allowed.get("notify_channel") or ""))
        if channel is None:
            allowed.pop("notify_channel")
        else:
            allowed["notify_channel"] = channel


def _overlay(settings: Settings) -> Settings:
    path = settings.user_config_path
    if not path.exists():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    allowed = {k: data[k] for k in USER_KEYS if k in data}
    if not allowed:
        return settings
    if "downloader" in allowed:
        allowed["downloader"] = normalize_downloader(str(allowed["downloader"]))
    _keep_paths(allowed)
    return settings.model_copy(update=allowed)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    s = _overlay(s)
    s.ensure_dirs()
    return s


def save_user_config(settings: Settings, updates: dict) -> Settings:
    allowed = {k: updates[k] for k in USER_KEYS if k in updates}
    if "downloader" in allowed:
        allowed["downloader"] = normalize_downloader(str(allowed["downloader"]))
    _keep_paths(allowed)
    merged = settings.model_copy(update=allowed)
    merged.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {}
    for k in USER_KEYS:
        v = getattr(merged, k)
        payload[k] = str(v) if isinstance(v, Path) else v
    merged.user_config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged

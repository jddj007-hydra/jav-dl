from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

USER_KEYS = (
    "proxy_enabled",
    "proxy_url",
    "javbus_base",
    "clm_home",
    "clm_search",
    "downloader",
    "xunlei_url",
    "xunlei_username",
    "xunlei_password",
    "xunlei_device_name",
    "scrape_enabled",
    "media_dir",
    "tpdb_api_key",
)

DOWNLOADERS = ("aria2", "xunlei")


def normalize_downloader(value: str | None) -> str:
    v = (value or "aria2").strip().lower()
    return v if v in DOWNLOADERS else "aria2"


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
    auth_user: str = ""
    auth_pass: str = ""
    metadata_ttl: int = 86400
    latest_ttl: int = 7200
    tpdb_api_key: str = ""
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
            "download_dir": str(self.download_dir),
            "aria2_rpc": self.aria2_rpc,
            "downloader": normalize_downloader(self.downloader),
            "xunlei_url": self.xunlei_url,
            "xunlei_username": self.xunlei_username,
            "xunlei_password_set": bool(self.xunlei_password),
            "xunlei_device_name": self.xunlei_device_name,
            "scrape_enabled": bool(self.scrape_enabled),
            "media_dir": str(self.media_dir),
            "scrape_settle_seconds": int(self.scrape_settle_seconds),
            "scrape_min_mb": int(self.scrape_min_mb),
            "auth_enabled": bool(self.auth_user and self.auth_pass),
            "tpdb_api_key_set": bool(self.tpdb_api_key),
        }


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
    if "media_dir" in allowed:
        raw = str(allowed["media_dir"]).strip()
        if not raw:
            allowed.pop("media_dir")
        else:
            allowed["media_dir"] = Path(raw)
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
    if "media_dir" in allowed:
        raw = str(allowed["media_dir"]).strip()
        if not raw:
            allowed.pop("media_dir")
        else:
            allowed["media_dir"] = Path(raw)
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

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResponse(BaseModel):
    code: str
    metadata: dict | None = None
    error: str | None = None
    cached: bool = False


class ResourceItem(BaseModel):
    rank: int = 0
    info_hash: str
    title: str
    size: str
    size_bytes: int | None = None
    heat: int = 0
    date: str = ""
    ext: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "clm"
    magnet: str = ""
    pack: bool = False


class ResourcesResponse(BaseModel):
    code: str
    items: list[ResourceItem]
    error: str | None = None


class ClearDownloads(BaseModel):
    status: str


class BatchText(BaseModel):
    text: str = ""


class BatchEnqueueItem(BaseModel):
    code: str = ""
    info_hash: str = ""
    title: str = ""


class BatchEnqueue(BaseModel):
    items: list[BatchEnqueueItem] = Field(default_factory=list)


class DownloadRequest(BaseModel):
    code: str = ""
    info_hash: str
    title: str = ""
    kind: str = ""
    tpdb_id: str = ""
    site: str = ""
    date: str = ""
    work_title: str = ""
    tpdb_kind: str = ""
    performers: list[str] = []
    pick_token: str = ""
    file_indexes: list[int] | None = None


class SettingsUpdate(BaseModel):
    proxy_enabled: bool | None = None
    proxy_url: str | None = None
    javbus_base: str | None = None
    clm_home: str | None = None
    clm_search: str | None = None
    clm_search_backup: str | None = None
    downloader: str | None = None
    xunlei_url: str | None = None
    xunlei_username: str | None = None
    xunlei_password: str | None = None
    xunlei_device_name: str | None = None
    scrape_enabled: bool | None = None
    media_dir: str | None = None
    western_media_dir: str | None = None
    scrape_settle_seconds: int | None = Field(default=None, ge=0)
    scrape_min_mb: int | None = Field(default=None, ge=0)
    tpdb_api_key: str | None = None

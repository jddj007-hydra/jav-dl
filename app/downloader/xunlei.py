from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urljoin

UIAUTH_RE = re.compile(r'function uiauth\(value\)\{\s*return "([^"]+)"')


class XunleiError(Exception):
    pass


def file_index_from_list(listed: dict) -> str:
    resources = ((listed.get("list") or {}).get("resources") or [])
    if not resources:
        raise XunleiError("迅雷未能解析这个磁力")
    res = resources[0]
    meta = res.get("meta") or {}
    err = meta.get("error") or ""
    if err and not res.get("file_size"):
        raise XunleiError(f"迅雷解析磁力失败：{err}")
    count = int(res.get("file_count") or 1)
    if count <= 1:
        return "--1,"
    max_idx = 0
    for sub in ((res.get("dir") or {}).get("resources") or []):
        idx = sub.get("file_index")
        if isinstance(idx, int) and idx > max_idx:
            max_idx = idx
        elif isinstance(idx, str) and idx.isdigit() and int(idx) > max_idx:
            max_idx = int(idx)
    if max_idx <= 0:
        max_idx = count - 1
    return f"0-{max_idx}"


def map_phase(task: dict) -> str:
    phase = (task.get("phase") or "").upper()
    raw = task.get("params") or {}
    status_raw = raw.get("status") or ""
    nested = ""
    if isinstance(status_raw, str) and status_raw.startswith("{"):
        try:
            nested = str((json.loads(status_raw) or {}).get("phase") or "").lower()
        except json.JSONDecodeError:
            nested = status_raw.lower()
    elif isinstance(status_raw, dict):
        nested = str(status_raw.get("phase") or "").lower()
    blob = f"{phase} {nested}"
    if "COMPLETE" in phase or nested in {"complete", "completed", "finish", "finished"}:
        return "complete"
    if "PAUSE" in phase or nested in {"paused", "pause"}:
        return "paused"
    if "ERROR" in phase or nested in {"error", "failed"} or raw.get("error_detail"):
        return "error"
    if "PENDING" in phase or nested in {"pending", "waiting", "queue"}:
        return "waiting"
    return "active"


def task_view(task: dict) -> dict:
    params = task.get("params") or {}
    status = map_phase(task)
    total = int(task.get("file_size") or 0)
    done = int(params.get("checked_size") or 0)
    try:
        speed = int(str(params.get("speed") or "0").split(".")[0])
    except ValueError:
        speed = 0
    err = params.get("error_detail") or ""
    return {
        "gid": str(task.get("id") or ""),
        "status": status,
        "totalLength": str(total),
        "completedLength": str(done),
        "downloadSpeed": str(speed),
        "errorMessage": err if status == "error" else "",
        "connections": 0,
        "numSeeders": 0,
        "truncated": False,
    }


def view_task(gid: str, index: dict[str, dict], truncated: bool) -> dict:
    task = index.get(str(gid))
    if task:
        return task_view(task)
    return {
        "gid": gid,
        "status": "missing",
        "truncated": bool(truncated),
        "totalLength": "0",
        "completedLength": "0",
        "downloadSpeed": "0",
        "errorMessage": "迅雷任务不存在或已删",
        "connections": 0,
        "numSeeders": 0,
    }


class Xunlei:
    def __init__(self, settings):
        self.base = (settings.xunlei_url or "").rstrip("/")
        self.username = settings.xunlei_username
        self.password = settings.xunlei_password
        self.device_name = settings.xunlei_device_name
        self._token = ""
        self._token_at = 0.0
        self._device_id_cache = ""
        self._folder_id_cache = ""

    def _auth(self) -> tuple[str, str] | None:
        if self.username or self.password:
            return (self.username, self.password)
        return None

    def _cgi(self, path: str) -> str:
        return urljoin(self.base + "/", f"webman/3rdparty/pan-xunlei-com/index.cgi/{path.lstrip('/')}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Any = None,
        timeout: float = 25.0,
    ) -> httpx.Response:
        if not self.base:
            raise XunleiError("未配置迅雷地址")
        headers = {"Accept": "application/json", "User-Agent": "jav-dl"}
        if token:
            headers["pan-auth"] = token
        url = path if path.startswith("http") else self._cgi(path)
        if token and "pan_auth=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}pan_auth={quote(token)}&device_space="
        import httpx
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                verify=False,
                auth=self._auth(),
            ) as client:
                return await client.request(method, url, headers=headers, json=json_body)
        except httpx.HTTPError as e:
            raise XunleiError(f"连不上迅雷面板 {self.base}") from e

    async def _json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Any = None,
        timeout: float = 25.0,
    ) -> dict:
        r = await self._request(method, path, token=token, json_body=json_body, timeout=timeout)
        if r.status_code == 401:
            raise XunleiError("迅雷面板账号密码不对")
        try:
            data = r.json() if r.content else {}
        except json.JSONDecodeError as e:
            raise XunleiError(f"迅雷返回了无法解析的内容（HTTP {r.status_code}）") from e
        if r.status_code >= 400:
            err = data.get("error_description") or data.get("error") or r.text[:200]
            raise XunleiError(f"迅雷接口失败：{err}")
        return data if isinstance(data, dict) else {}

    async def token(self) -> str:
        if self._token and time.time() - self._token_at < 500:
            return self._token
        r = await self._request("GET", "")
        if r.status_code == 401:
            raise XunleiError("迅雷面板账号密码不对")
        m = UIAUTH_RE.search(r.text or "")
        if not m:
            raise XunleiError("迅雷页面里没有找到 pan_auth，确认面板已登录迅雷账号")
        self._token = m.group(1)
        self._token_at = time.time()
        return self._token

    async def version(self) -> str | None:
        try:
            data = await self._json("GET", "launcher/status")
        except XunleiError:
            return None
        return str(data.get("running_version") or "") or None

    async def _tasks(self, token: str, filters: str) -> tuple[list[dict], bool]:
        out: list[dict] = []
        page = ""
        for _ in range(8):
            q = f"drive/v1/tasks?{filters}&limit=100&device_space="
            if page:
                q += f"&page_token={quote(page)}"
            data = await self._json("GET", q, token=token)
            out.extend(data.get("tasks") or [])
            page = data.get("next_page_token") or ""
            if not page:
                return out, False
        return out, True

    async def _download_tasks(self, token: str) -> tuple[list[dict], bool]:
        # 不带 type/space 时返回的是整个账号的云盘离线任务，NAS 上的任务会被淹没
        device_id = await self._device_id(token)
        return await self._tasks(token, f"type=user%23download-url&space={quote(device_id)}")

    async def list_download_tasks(self) -> tuple[dict[str, dict], bool]:
        token = await self.token()
        tasks, truncated = await self._download_tasks(token)
        index = {str(task.get("id") or ""): task for task in tasks if task.get("id")}
        return index, truncated

    async def _runner(self, token: str) -> dict:
        runners, _truncated = await self._tasks(token, "type=user%23runner")
        if not runners:
            raise XunleiError("迅雷还没有在线设备，先在面板里登录迅雷账号")
        want = (self.device_name or "").strip()
        if want:
            for t in runners:
                if want in str(t.get("name") or ""):
                    return t
        for t in runners:
            name = str(t.get("name") or "")
            if name.startswith("群晖-") or "xunlei" in name.lower():
                return t
        return runners[0]

    async def _device_id(self, token: str) -> str:
        if self._device_id_cache:
            return self._device_id_cache
        runner = await self._runner(token)
        target = str((runner.get("params") or {}).get("target") or "")
        if not target:
            raise XunleiError("读不到迅雷设备 ID")
        self._device_id_cache = target
        return target

    async def _parent_folder_id(self, token: str) -> str:
        if self._folder_id_cache:
            return self._folder_id_cache
        device_id = await self._device_id(token)
        folders = quote(json.dumps({"kind": {"eq": "drive#folder"}}, separators=(",", ":")))
        try:
            data = await self._json(
                "GET",
                f"drive/v1/files?space={quote(device_id)}&limit=100&parent_id=&filters={folders}&device_space=",
                token=token,
            )
        except XunleiError:
            data = {}
        for f in data.get("files") or []:
            fid = str(f.get("id") or "")
            if fid and str(f.get("name") or "") == "downloads":
                self._folder_id_cache = fid
                return fid
        tasks, _truncated = await self._download_tasks(token)
        for t in tasks:
            params = t.get("params") or {}
            fid = str(params.get("parent_folder_id") or "")
            path = str(params.get("parent_folder_path") or "")
            if fid and (not path or path.rstrip("/") == "/downloads"):
                self._folder_id_cache = fid
                return fid
        raise XunleiError("找不到迅雷下载目录，先在迅雷面板里手动下过一次")

    async def add_magnet(self, magnet: str, dest: str) -> str:
        token = await self.token()
        listed = await self._json(
            "POST",
            "drive/v1/resource/list",
            token=token,
            json_body={"urls": magnet},
            timeout=40.0,
        )
        resources = ((listed.get("list") or {}).get("resources") or [])
        if not resources:
            raise XunleiError("迅雷未能解析这个磁力")
        res = resources[0]
        name = str(res.get("name") or dest or "download")
        file_size = int(res.get("file_size") or 0)
        file_count = int(res.get("file_count") or 1)
        index = file_index_from_list(listed)
        device_id = await self._device_id(token)
        parent = await self._parent_folder_id(token)
        payload = {
            "type": "user#download-url",
            "name": name,
            "file_name": name,
            "file_size": str(file_size),
            "space": device_id,
            "params": {
                "target": device_id,
                "url": magnet,
                "total_file_count": str(file_count),
                "sub_file_index": index,
                "file_id": "",
                "parent_folder_id": parent,
                "parent_folder_path": "/downloads/",
            },
        }
        data = await self._json(
            "POST",
            "drive/v1/task",
            token=token,
            json_body=payload,
            timeout=40.0,
        )
        task_id = str(data.get("id") or (data.get("task") or {}).get("id") or "")
        if not task_id:
            # 有的版本把任务写进 list，再按 info_hash 找回
            found = await self._find_by_magnet(token, magnet)
            if found:
                return found
            raise XunleiError("迅雷已受理但没有返回任务 ID")
        return task_id

    async def _find_by_magnet(self, token: str, magnet: str) -> str:
        needle = magnet.lower()
        tasks, _truncated = await self._download_tasks(token)
        for t in tasks:
            url = str((t.get("params") or {}).get("url") or "")
            if url.lower() == needle or needle[20:60] in url.lower():
                return str(t.get("id") or "")
        return ""

    async def tell(self, gid: str) -> dict:
        index, truncated = await self.list_download_tasks()
        return view_task(gid, index, truncated)

    async def pause(self, gid: str) -> None:
        await self._task_op("pause", gid)

    async def resume(self, gid: str) -> None:
        await self._task_op("resume", gid)

    async def remove(self, gid: str) -> None:
        try:
            await self._task_op("delete", gid)
        except XunleiError:
            token = await self.token()
            await self._json(
                "POST",
                "drive/v1/tasks:delete",
                token=token,
                json_body={"ids": [gid]},
            )

    async def _task_op(self, op: str, gid: str) -> None:
        token = await self.token()
        try:
            await self._json(
                "POST",
                f"drive/v1/tasks:{op}",
                token=token,
                json_body={"ids": [gid]},
            )
        except XunleiError as e:
            raise XunleiError(f"迅雷不支持{op}：{e}") from e

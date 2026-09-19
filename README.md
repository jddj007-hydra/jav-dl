# jav-dl

番号 / 关键词查询 + 磁力猫搜种 + 后台 BT 下载。浏览器只是控制台，关掉也不影响任务。

```text
输入番号或女优名
        │
        ├─ 番号  → JavBus 元数据 + 磁力猫磁链（按规则排序）→ 点选下载
        └─ 关键词 → JavBus 作品列表 → 点进番号详情
                                      │
                                      ▼
                               aria2（默认）或群晖迅雷
```

当前版本 `2.0.0`。已经不是 Claude skill，也没有文本解密。

## 跑起来

### Docker（推荐）

```bash
docker compose up -d --build
```

打开 http://localhost:8787

下载文件在 Docker volume `jav-downloads` 里。宿主机有 Clash 时，可在设置页打开代理，地址用 `http://host.docker.internal:7890`。**代理只用于查站，BT 不走代理。**

### 本机

需要 Python 3.12+。aria2 用于真正下载，没有的话仍可查询元数据和磁链。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 另开一个终端（可选）
aria2c --enable-rpc --rpc-listen-port=6800 --rpc-secret=jav-dl-rpc \
  --dir=./downloads --seed-time=0 --file-allocation=none --bt-max-peers=300

./run.sh
```

默认下载目录是项目下的 `./downloads/{番号}/`。刮削归档默认写到 `./media/YYYYMM/{番号}/`。

## 使用

1. 输入番号（`ssis001` / `SSIS-001`）或关键词（女优名、片名）
2. 番号：封面、片名、女优、预览图；关键词：先出作品列表，点一张进详情
3. 下面是磁力猫结果，已按 **无码破解 > 无码 > 中文 > 热度 > 体积** 排好，第一行高亮但不会自动下
4. 点下载，到「队列」看进度；合集 / 超过约 15GB 的条目会排到后面
5. 下载完成后自动刮削：写 NFO / `poster.jpg` / `fanart.jpg`，视频改名为番号，归档到 `media/YYYYMM/番号/`
6. 搜索番号或关键词时，库里已有的作品会标「已有」，不会禁止再下

## 它怎么工作

```text
浏览器  搜索 / 队列 / 设置
   │
   ▼
FastAPI :8787
   ├── /api/search      JavBus 元数据或关键词作品列表（SQLite 缓存 24h）
   ├── /api/resources   磁力猫磁链 + 排序
   ├── /api/downloads   入队 / 暂停 / 继续 / 取消
   ├── /api/img         封面代理 + 磁盘缓存
   ├── /api/settings    代理和站点地址（写入 data/config.json）
   └── /api/health      aria2 / 迅雷 / JavBus / 磁力猫
           │
           ├── data/jav-dl.db      元数据缓存、下载任务
           ├── data/img_cache/     封面
           └── aria2 RPC 或 群晖迅雷面板
```

| 目录 | 职责 |
|------|------|
| `app/codes.py` | 番号规范化：`ssis001` → `SSIS-001` |
| `app/sources/javbus.py` | JavBus HTML 解析（详情 + 搜索） |
| `app/sources/clm.py` | 磁力猫搜索（atob 包装页、base32 id → info_hash） |
| `app/ranking.py` | 磁链排序和 UC/U/C/合集标签 |
| `app/downloader/jobs.py` | 任务状态机，对接 aria2 / 迅雷 |
| `app/scrape.py` | 下载完成后写 NFO/封面，归档到 `YYYYMM/番号/` |
| `app/library.py` | 扫描归档目录，搜索时标「库里已有」 |
| `app/downloader/aria2.py` | aria2 JSON-RPC |
| `app/downloader/xunlei.py` | 群晖套件迅雷面板（环境变量切换） |
| `app/static/` | 单页：搜索、队列、设置 |
| `aria2/` | Docker 里的 aria2 镜像 |

查站走 HTTP 代理（可选），磁力只拼 `magnet:?xt=urn:btih:` + 公共 tracker，BT 本身不走代理。

## 配置

优先级：环境变量 / `.env` → 设置页写入的 `data/config.json`（后者覆盖代理和站点地址）。

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | `8787` | Web 端口（本机 `run.sh`） |
| `DATA_DIR` | `./data` | SQLite、封面缓存、用户配置 |
| `DOWNLOAD_DIR` | `./downloads` | 本机下载根目录 |
| `MEDIA_DIR` | `./media` | 刮削归档根目录（`YYYYMM/番号/`） |
| `SCRAPE_ENABLED` | `true` | 下载完成后是否刮削归档 |
| `ARIA2_RPC` | `http://127.0.0.1:6800/jsonrpc` | Docker 里是 `http://aria2:6800/jsonrpc` |
| `ARIA2_SECRET` | `jav-dl-rpc` | 与 aria2 RPC 密钥一致 |
| `PROXY_ENABLED` | `false` | 查站代理 |
| `PROXY_URL` | `http://127.0.0.1:7890` | Docker 里可写 `http://host.docker.internal:7890` |
| `AUTH_USER` / `AUTH_PASS` | 空 | 同时非空则开 HTTP Basic |
| `DOWNLOADER` | `aria2` | `aria2` 或 `xunlei` |
| `XUNLEI_URL` | `http://127.0.0.1:2345` | 群晖迅雷面板 |
| `XUNLEI_USERNAME` / `XUNLEI_PASSWORD` | 空 | 面板账号 |
| `XUNLEI_DEVICE_NAME` | `群晖-xunlei` | 用来匹配在线设备 |

下载器可以在设置页切换。切到迅雷前需要面板已登录，并且手动下过一次以便识别下载目录。进行中的任务不会跟着换后端。

刮削在设置页开关。归档目录可改成 jav-search 的 media，两边共用一个 Emby 库；那样请关掉 jav-search 对 jav-dl 下载目录的监控，避免抢文件。月份取自发行日期，没有则用刮削当天。完成后再静置约 60 秒才搬文件。

## 和 1.x skill 的差别

- 不再是 Claude skill，也没有叙事文本解密
- 种子只走 **磁力猫**（不再用 Nyaa）
- 下载交给独立的 aria2（或群晖迅雷），Web 只负责点选和看进度
- 支持女优/关键词搜作品，不只贴番号

## 测试

```bash
python3 -m pytest
```

覆盖番号规范化、磁链排序、JavBus/磁力猫 HTML 解析、迅雷文件索引。不打真实站点。

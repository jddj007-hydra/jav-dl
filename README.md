# jav-dl

番号 / 关键词 / 欧美片名查询 + 磁力猫搜种 + 后台 BT 下载。浏览器只是控制台，关掉也不影响任务。

```text
顶栏：JAV · 欧美 · 队列 · 设置

JAV
  打开即拉 JavBus 最新（有码 / 无码，可翻页）
  番号  → 详情 + 磁力猫（无码/中字/热度）
  关键词 → 作品列表 → 点进番号

欧美
  打开即拉 ThePornDB 最新（场景 / 电影，要 token）
  片名或演员 → 作品列表
  点一张 → 详情 + 磁力猫（按热度，合集靠后）
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

日本片落在 `./downloads/{番号}/`，刮削后归档到 `./media/YYYYMM/{番号}/`。欧美片落在 `./downloads/western/{目录名}/`，不按番号刮削。

## 使用

### JAV

1. 打开页面就是有码最新一页，可切「无码」、翻页，或点「最新」回到第一页
2. 也可以输入番号（`ssis001` / `SSIS-001`）或关键词（女优名、片名）
3. 番号：封面、片名、女优、预览图；关键词：先出作品列表，点一张进详情
4. 磁链来自磁力猫，按 **无码破解 > 无码 > 中文 > 热度 > 体积** 排，第一行高亮但不会自动下
5. 库里已有的作品会标「已有」，不会禁止再下

### 欧美

1. 设置里填写 ThePornDB token（账号在 [theporndb.net](https://theporndb.net) 自己生成）。留空表示不改已保存的 token
2. 打开「欧美」即拉场景最新；可切「电影」、翻页，或用片名 / 演员搜索
3. 点一张看片商、演员、简介，再用「片商 + 标题」查磁力猫。按热度排，合集和大约 15GB 以上靠后，不用无码 / 中字那套规则
4. 没有「库里已有」。下载目录是 `western/{片商-日期-标题}`。配置了 `WESTERN_MEDIA_DIR` 后，下完会按片商归档到该目录（`片商/文件名.nfo` 和 `文件名-poster.jpg`），不写进 `YYYYMM/番号/`

两边的下载都进同一个队列。点下载后到「队列」看进度。

## 它怎么工作

```text
浏览器  JAV / 欧美 / 队列 / 设置
   │
   ▼
FastAPI :8787
   ├── /api/search            JavBus 番号或关键词（SQLite 缓存 24h）
   ├── /api/jav/latest        JavBus 有码 / 无码最新（缓存 2h）
   ├── /api/western/latest    ThePornDB 场景 / 电影最新（缓存 2h）
   ├── /api/western/search    ThePornDB 片名或演员
   ├── /api/western/{kind}/{id}
   ├── /api/resources         磁力猫：?code= 番号排序，?q= 关键词按热度
   ├── /api/downloads         入队 / 暂停 / 继续 / 取消
   ├── /api/img               封面代理 + 磁盘缓存
   ├── /api/settings          代理、站点、ThePornDB token（写入 data/config.json）
   └── /api/health            aria2 / 迅雷 / JavBus / 磁力猫 / token 是否已填
           │
           ├── data/jav-dl.db      元数据缓存、下载任务
           ├── data/img_cache/     封面
           └── aria2 RPC 或 群晖迅雷面板
```

| 目录 | 职责 |
|------|------|
| `app/codes.py` | 番号规范化：`ssis001` → `SSIS-001` |
| `app/slug.py` | 欧美下载目录名，保证不是合法番号 |
| `app/sources/javbus.py` | JavBus HTML 解析（详情、搜索、最新列表） |
| `app/sources/tpdb.py` | ThePornDB 场景 / 电影 |
| `app/sources/clm.py` | 磁力猫搜索（atob 包装页、base32 id → info_hash） |
| `app/ranking.py` | 番号磁链的 UC/U/C 排序；关键词磁链按热度 |
| `app/downloader/jobs.py` | 任务状态机，对接 aria2 / 迅雷 |
| `app/scrape.py` | 带番号的文件写 NFO/封面，归档到 `YYYYMM/番号/`；跳过 `western/` |
| `app/library.py` | 扫描归档目录，JAV 搜索时标「库里已有」 |
| `app/downloader/aria2.py` | aria2 JSON-RPC |
| `app/downloader/xunlei.py` | 群晖套件迅雷面板（环境变量切换） |
| `app/static/` | 单页：JAV、欧美、队列、设置 |
| `aria2/` | Docker 里的 aria2 镜像 |

查站走 HTTP 代理（可选），磁力只拼 `magnet:?xt=urn:btih:` + 公共 tracker，BT 本身不走代理。

ThePornDB 基址是 `https://api.theporndb.net`，请求头 `Authorization: Bearer <token>`。最新列表按 `orderBy=recently_released`。封面代理允许 `theporndb.net` 和 `metadataapi.net`。

## 配置

优先级：环境变量 / `.env` → 设置页写入的 `data/config.json`（后者覆盖代理、站点地址和 token）。

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | `8787` | Web 端口（本机 `run.sh`） |
| `DATA_DIR` | `./data` | SQLite、封面缓存、用户配置 |
| `DOWNLOAD_DIR` | `./downloads` | 本机下载根目录 |
| `MEDIA_DIR` | `./media` | 刮削归档根目录（`YYYYMM/番号/`） |
| `SCRAPE_ENABLED` | `true` | 带番号的文件是否刮削归档 |
| `WESTERN_MEDIA_DIR` | 空 | 欧美归档根目录。空则只下载不归档。生产上是 6T 的 `欧美` |
| `TPDB_API_KEY` | 空 | ThePornDB token，也可只在设置页填写 |
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

刮削在设置页开关。只处理文件名或文件夹里能抽出唯一番号的视频。`downloads/western/` 整目录跳过。归档目录可改成和别的库共用的 media；若另一边也在监控同一下载目录，请关掉其中一边，避免抢文件。月份取自发行日期，没有则用刮削当天。完成后再静置约 60 秒才搬文件。

## 和 1.x skill 的差别

- 不再是 Claude skill，也没有叙事文本解密
- 种子只走 **磁力猫**（不再用 Nyaa）
- 下载交给独立的 aria2（或群晖迅雷），Web 只负责点选和看进度
- JAV 支持女优/关键词和最新列表，不只贴番号
- 欧美走 ThePornDB 元数据，磁链仍是磁力猫，不和番号归档混在一起

## 测试

```bash
python3 -m pytest
```

覆盖番号规范化、磁链排序、JavBus/磁力猫 HTML 解析、ThePornDB 字段映射、欧美目录名、迅雷文件索引。不打真实站点。

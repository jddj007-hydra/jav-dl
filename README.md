# jav-dl

番号 / 关键词 / 欧美片名查询 + 磁力猫搜种 + 后台 BT 下载。浏览器只是控制台，关掉也不影响任务。

```text
顶栏：JAV · 欧美 · 媒体库 · 追更 · 队列 · 设置

JAV
  打开即拉 JavBus 最新（有码 / 无码，平面 / VR，可翻页）
  番号  → 详情 + 磁力猫（无码/中字/热度）
  关键词 → 作品列表 → 点进番号

欧美
  打开即拉 ThePornDB 最新（场景 / 电影，平面 / VR，要 token）
  片名、演员或片商 → 作品列表
  点一张 → 详情 + 磁力猫按片商搜索（片名相近的靠前）
                                      │
                                      ▼
                    aria2（默认）或网页迅雷
                    https://github.com/cnk3x/xunlei
```

当前版本 `2.0.0`。

## 跑起来

### Docker（推荐）

```bash
docker compose up -d --build
```

打开 http://localhost:8787。web 服务带 healthcheck，打的是本机 `/api/health`；设了账号密码时会带上 Basic。

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

日本片落在 `./downloads/{番号}/`。平面刮削到 `./media/YYYYMM/{番号}/`，番号 VR 刮削到 `vrporn/jav/{厂牌}/{番号}/`（见 `VR_MEDIA_DIR`）。欧美片落在 `./downloads/western/{目录名}/`，不按番号刮削；平面和 VR 再分别进欧美目录和 `vrporn/western/{片商}/`。

## 使用

### JAV

1. 打开页面就是有码平面最新一页，可切「无码」、「平面 / VR」、翻页，或点「最新」回到第一页。VR 最新走 JavBus 的 VR専用 列表
2. 也可以输入番号（`ssis001` / `SSIS-001`）或关键词（女优名、片名）。列表和搜索都按当前的平面 / VR 筛；直接查一个番号仍打开详情
3. 番号：封面、片名、女优、预览图；关键词：先出作品列表，点一张进详情
4. 磁链来自磁力猫，按 **无码破解 > 无码 > 中文 > 热度 > 体积** 排，第一行高亮但不会自动下
5. 库里已有的作品会标「已有」，不会禁止再下

### 欧美

1. 设置里填写 ThePornDB token（账号在 [theporndb.net](https://theporndb.net) 自己生成）。留空表示不改已保存的 token
2. 打开「欧美」即拉场景平面最新；可切「电影」、「平面 / VR」、翻页，或用片名 / 演员 / 片商搜索。片商名对得上会直接打开该站目录
3. 点一张看片商、演员、简介，磁链按片商去磁力猫搜。片名或演员对得上的排前面，合集和大约 15GB 以上靠后
4. 库里已有的会标「已有」，仍可以再下。标了 suck 的不会再入队。下载目录是 `western/{片商-日期-标题}`。平面归档到 `WESTERN_MEDIA_DIR/{片商}/`，VR 归档到 `VR_MEDIA_DIR/western/{片商}/`（`文件名.nfo` 和 `文件名-poster.jpg`），都不写进 `YYYYMM/番号/`

两边的下载都进同一个队列。点下载后到「队列」看进度。媒体库分四块：番号（按月份）、番号VR（按厂牌）、欧美（按片商）、VR（欧美 VR，按片商）。追更页可以关注女优、系列、片商，以及欧美演员和片商。

## 它怎么工作

```text
浏览器  JAV / 欧美 / 媒体库 / 追更 / 队列 / 设置
   │
   ▼
FastAPI :8787
   ├── /api/search            JavBus 番号或关键词（SQLite 缓存 24h；列表可带 format=flat|vr）
   ├── /api/jav/latest        JavBus 有码 / 无码最新（缓存 2h；format=vr 走 VR専用）
   ├── /api/western/latest    ThePornDB 场景 / 电影最新（缓存 2h；format=flat|vr）
   ├── /api/western/search    ThePornDB 片名、演员或片商
   ├── /api/western/{kind}/{id}
   ├── /api/resources         磁力猫：?code= 番号排序，?q= 关键词按热度
   ├── /api/downloads         入队 / 暂停 / 继续 / 取消；可一次贴多个番号，预览后再入队。多文件种子先勾选文件
   ├── /api/downloads/events  队列状态推送。页面在后台时断开，断线后退回大约 30 秒拉一次
   ├── /api/subscriptions     追更：女优、系列、片商、欧美演员和片商。默认只提醒
   ├── /api/library           媒体库：番号按月份，番号VR按厂牌，欧美 / VR 按片商
   ├── /api/player            播放器片库 movies / scenes / stamp（shelf；format=flat|vr|all，默认 all）
   ├── /api/img               封面代理 + 磁盘缓存
   ├── /api/settings          代理、站点、ThePornDB token（写入 data/config.json）
   └── /api/health            aria2 / 迅雷 / JavBus / 磁力猫 / token 是否已填
           │
           ├── data/jav-dl.db      元数据缓存、下载任务
           ├── data/img_cache/     封面
           └── aria2 RPC 或网页迅雷 https://github.com/cnk3x/xunlei
```

| 目录 | 职责 |
|------|------|
| `app/codes.py` | 番号规范化：`ssis001` → `SSIS-001`；番号 VR 厂牌 |
| `app/studios.py` | 欧美片商前缀、VR 片商判定 |
| `app/slug.py` | 欧美下载目录名，保证不是合法番号 |
| `app/sources/javbus.py` | JavBus HTML 解析（详情、搜索、最新列表、VR専用） |
| `app/sources/tpdb.py` | ThePornDB 场景 / 电影（平面 / VR 列表） |
| `app/sources/clm.py` | 磁力猫搜索（atob 包装页、base32 id → info_hash、十分钟结果缓存、备用域） |
| `app/ranking.py` | 番号磁链的 UC/U/C 排序；关键词磁链按热度 |
| `app/downloader/jobs.py` | 任务状态机，对接 aria2 / 迅雷 |
| `app/scrape.py` | 带番号的文件写 NFO/封面；平面进 `YYYYMM/番号/`，番号 VR 进 `vrporn/jav/{厂牌}/{番号}/`；跳过 `western/` |
| `app/western_archive.py` | 欧美刮削：平面进欧美目录，VR 进 `vrporn/western/{片商}/` |
| `app/rehome.py` | 把误进平面库的 VR 迁到 vrporn |
| `app/library.py` | 扫描四套归档目录，搜索时标「库里已有」 |
| `app/downloader/aria2.py` | aria2 JSON-RPC |
| `app/downloader/xunlei.py` | 网页迅雷面板，项目 [cnk3x/xunlei](https://github.com/cnk3x/xunlei) |
| `app/static/` | 单页：JAV、欧美、媒体库、追更、队列、设置 |
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
| `MEDIA_DIR` | `./media` | 番号平面归档根目录（`YYYYMM/番号/`） |
| `SCRAPE_ENABLED` | `true` | 带番号的文件是否刮削归档 |
| `SCRAPE_SETTLE_SECONDS` | `60` | 下完后再静置这么久才归档。设置页可改，留空保存则保留原值 |
| `SCRAPE_MIN_MB` | `50` | 小于这个体积不当成正片。设置页可改，留空保存则保留原值 |
| `WESTERN_MEDIA_DIR` | 空 | 欧美平面归档根目录。空则只下载不归档。设置页可改，留空保存则保留原值 |
| `VR_MEDIA_DIR` | 空 | VR 归档根目录，现网是 `vrporn`。番号 VR 进 `jav/{厂牌}/{番号}/`，欧美 VR 进 `western/{片商}/`。空则番号 VR 进平面库，欧美 VR 退回欧美平面目录。旧值若写成 `vrporn/western` 仍按上一级理解。设置页可改，留空保存则保留原值 |
| `TPDB_API_KEY` | 空 | ThePornDB token，也可只在设置页填写 |
| `NOTIFY_CHANNEL` | 空 | `telegram`、`bark`、`serverchan` 之一。空则不通知 |
| `NOTIFY_TELEGRAM_TOKEN` / `NOTIFY_TELEGRAM_CHAT` | 空 | Telegram 机器人 token 和 chat id |
| `NOTIFY_BARK_URL` | 空 | Bark key，或完整地址如 `https://api.day.app/key` |
| `NOTIFY_SERVERCHAN_KEY` | 空 | Server酱 SendKey |
| `ARIA2_RPC` | `http://127.0.0.1:6800/jsonrpc` | Docker 里是 `http://aria2:6800/jsonrpc` |
| `ARIA2_SECRET` | `jav-dl-rpc` | 与 aria2 RPC 密钥一致 |
| `CLM_SEARCH_BACKUP` | 空 | 主搜索域失败或没有结果时再用的磁力猫域名。设置页可改，留空保存会清掉 |
| `PROXY_ENABLED` | `false` | 查站代理 |
| `PROXY_URL` | `http://127.0.0.1:7890` | Docker 里可写 `http://host.docker.internal:7890` |
| `AUTH_USER` / `AUTH_PASS` | 空 | 同时非空则开 HTTP Basic，`/api/health` 也要登录。都空着时启动会警告 |
| `DOWNLOADER` | `aria2` | `aria2` 或 `xunlei` |
| `XUNLEI_URL` | `http://127.0.0.1:2345` | [cnk3x/xunlei](https://github.com/cnk3x/xunlei) 面板地址 |
| `XUNLEI_USERNAME` / `XUNLEI_PASSWORD` | 空 | 面板账号 |
| `XUNLEI_DEVICE_NAME` | `群晖-xunlei` | 用来匹配在线设备 |

下载器可以在设置页切换。网页离线迅雷是 [cnk3x/xunlei](https://github.com/cnk3x/xunlei)，`XUNLEI_URL` 填它的面板地址。切过去之前，面板要已登录迅雷账号，并且手动下过一次，才能认出下载目录。每条任务记下当时的下载器，之后切换只影响新任务。

通知在设置页选一种。下载完成、归档完成、失败各发一条，带番号或片名，失败带原因。通知失败只写日志。

追更在单独一页。女优可以写名字，系列和片商粘贴 JavBus 页面。欧美填演员或片商名字。第一次检查只记下现有作品。之后的新作默认只提醒；打开自动下载后，才按无码破解、中字和体积入队。库里或队列里已有的不重复下。

刮削在设置页开关。带番号的视频：平面进 `MEDIA_DIR/YYYYMM/番号/`，番号 VR（厂牌名带 VR，或 DSVR / SAVR / MKCK 这一类）进 `VR_MEDIA_DIR/jav/{厂牌}/{番号}/`。`downloads/western/` 整目录按欧美处理：平面进 `WESTERN_MEDIA_DIR/{片商}/`，VR 进 `VR_MEDIA_DIR/western/{片商}/`。归档目录可改成和别的库共用；若另一边也在监控同一下载目录，请关掉其中一边，避免抢文件。番号平面的月份取自发行日期，没有则用刮削当天。完成后再静置约 60 秒才搬文件。已经进错目录的 VR，可用 `app/rehome.py` 迁到 vrporn 再重扫媒体库。

## 测试

```bash
python3 -m pytest
```

覆盖番号规范化、磁链排序、JavBus/磁力猫 HTML 解析、ThePornDB 字段映射、欧美目录名、平面 / VR 拆分、迅雷文件索引。不打真实站点。

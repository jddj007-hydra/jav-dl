# 任务拆解

状态只允许 `todo`、`doing`、`done`、`blocked`。范围和验收见 `prd.md`。进度见 `status.md`。

| ID | 标题 | 优先级 | 里程碑 | 状态 | 依赖 | 建议落点 | DoD | 备注 |
|---|---|---|---|---|---|---|---|---|
| M1-T1 | JAV 封面拆成 fanart 原图和右裁 poster | P0 | M1 | done | | `app/scrape.py` `write_images`；`requirements.txt` 增加 Pillow；`tests/test_scrape.py` | 横图的 fanart 字节等于下载字节，poster 约 2:3 且取自右侧。竖图或解码失败时两份都是原图且不抛错。镜像能装上 Pillow | A1。裁切收在一个函数里，无码以后可在此分支；本阶段都走右裁 |
| M1-T2 | 归档时把预览图写入 extrafanart | P0 | M1 | done | | `app/scrape.py` 归档提交处读取 `meta["samples"]` | 有样图时出现 `extrafanart/fanart-01.jpg` 起的文件。超过 20 张只取前 20。一张失败只记日志。无样图不建空目录。已有同名文件不重复下载 | A2。请求可复用封面下载，但样图不进 `data/img_cache` |
| M1-T3 | NFO 演员写入已有头像 URL | P0 | M1 | done | | `app/nfo.py`；`tests/test_nfo.py`；`app/library.py` 演员解析保持只取名字 | 有 `photo` 的演员含 `<thumb>`，空 URL 不写空标签。库扫描出的演员列表与现在一致。磁盘上没有新头像文件 | A3 |
| M1-T4 | 重新刮削重写这三类侧车文件 | P0 | M1 | done | M1-T1, M1-T2, M1-T3 | `fill_jav_folder` / `_write_jav_sidecars` 仍缺了才写；`JobManager.rescrape` 允许覆盖该番号的 nfo、poster、fanart、extrafanart | 对一部旧目录重新刮削后三样侧车按新规则更新，视频还在。启动扫描和被动补缺不改写已有海报 | A1–A3 的入口区分 |
| M2-T1 | 磁链行展示排序理由 | P1 | M2 | todo | | `app/static/app.js` 的 JAV `renderResources` 与欧美磁链渲染 | JAV 行理由与 tags、heat、size、pack 一致，有 UC 时以 UC 开头。欧美无 UC 标签时理由里没有 UC。排序和高亮、下载目标不变 | C1。不改 `app/ranking.py` |
| M2-T2 | 队列标题点回详情 | P1 | M2 | todo | | `app/downloader/jobs.py` 公开字段补 `kind`、`tpdb_id`（读 `dest/.javdl.json`）；`app/static/app.js` `renderQueue` | 番号标题打开该番号详情。有 tpdb id 的欧美标题打开对应详情。没有 id 的不是链接。暂停、继续、取消、重新刮削、删除记录不变 | C2 |
| M2-T3 | 删除当前归档版本 | P0 | M2 | todo | | `app/routers/library_page.py` 新删除接口；`app/library.py` `remove_archived`；`delete_library` / `delete_western`；媒体库卡片按钮；`tests/test_library.py`、`tests/test_suck.py` | 删除后磁盘文件和库行都没了，同一作品可再入队。suck 表不变，`plays` 还在。越界路径失败且不删文件。重新刮削按钮仍在 | E1。确认文案写明删文件。不调用 `app/suck.py` |
| M3-T1 | 对不上时列出 TPDB 候选并停住自动重试 | P0 | M3 | todo | | `app/sources/tpdb.py` 候选函数；`app/db.py` 待确认表；`app/downloader/jobs.py` `watch_western` 与 `_scrape_western` | 唯一命中不写待确认，仍自动归档。歧义或无命中写表，重启后还在。待确认期间不再因该文件周期请求 ThePornDB。候选含 id、kind、标题、片商、日期、演员。无命中时列表可空，文件仍留在失败区 | C3。候选最多 8 条，复用现有 parse / 搜索 |
| M3-T2 | 点选候选后按现有欧美归档落盘 | P0 | M3 | todo | M3-T1 | 确认接口接收路径或 job id，加上选中的 id 和 kind；归档走 `app/western_archive.py` 已有详情写入 | 点选后视频进入对应片商目录，旁边有 nfo 和 `{stem}-poster.jpg`，库里能看到，待确认行消失。文件已不在下载目录时确认失败并说明。未选中的候选不写入 | C3。不另写一套搬文件逻辑 |
| M3-T3 | 队列页待确认区 | P0 | M3 | todo | M3-T1, M3-T2 | `app/static/index.html`、`app/static/app.js` 队列视图；递增 `app.js` 版本参数 | 浏览器能看见失败文件、点一条候选、归档后该行消失。空列表有「没有待确认的文件」。不提供一次提交多条。区域与下载失败、刮削失败分开 | C3 |
| M4-T1 | 用 hash 保存列表和库的筛选 | P0 | M4 | todo | | `app/static/app.js` 的 `route`、`hashchange`，以及现在用 `pushState` 打开详情和目录的位置 | 刷新后有码/无码、平面/VR、页码还在。返回列表不丢参数。`#/queue`、`#/follow`、`#/settings` 仍只打开对应页。前进后退与页内返回一致。没有第二份 localStorage 筛选 | B4、E2。参数只认一套：JAV `kind` `format` `page` `genre` `browse` `code`；欧美 `kind` `format` `page` `theme` `facet` `id`；库 `kind` `sort` `q` `actor` `studio` `series` `density`。未知参数忽略 |
| M4-T2 | 番号最新列表的类型芯片 | P1 | M4 | todo | M4-T1 | `app/static/index.html` `#jav-feed`；样式对齐 `#western-themes`；类型路径常量放前端或 `app/sources/javbus.py` 一处；点击走已有 `/api/jav/browse` | 平面最新能打开至少一个有码类型页并翻页，标题是该类型。`format=vr` 时看不到这组芯片，列表仍是 VR専用。类型页失败时用现有 JavBus 错误，页面不空白 | B1。芯片：中出、巨乳、熟女、制服、单体、痴女、多P、OL。实现时核对当前域名路径 |
| M4-T3 | 媒体库按演员、片商、系列筛选 | P1 | M4 | todo | M4-T1 | `app/routers/library_page.py` 条目带上已有的 `studio`、`series`；`app/static/app.js` `renderLibrary` / `libraryCard` | 点演员名后计数变成子集，hash 带上该演员，刷新仍是子集。片商、系列同样。清除后回全库。欧美页没有系列芯片。排序、关键字、suck 页仍可用 | B2。库内筛选，不跳 JavBus 关键字搜索 |
| M4-T4 | 封面疏密两档 | P1 | M4 | todo | M4-T1 | `app/static/style.css` `.works-grid`、`.lib-grid`；递增 `style.css` 版本参数 | JAV 墙、欧美墙、媒体库都能切两档。默认档等于现在的列宽和间距。比例不变（欧美库卡保持 16:9）。手机宽度下密档仍可点。没有 range 输入 | B3 |
| M5-T1 | 关注卡显示已记下的部数 | P1 | M5 | todo | | `app/db.py` `list_subscriptions` 增加 `known`；`app/static/app.js` `renderFollow` | 有 seen 行的订阅显示对应「已记下 N 部」。新建后的首次提示还在。检查后没有新作时数字不被清零 | D1。N 为 `subscription_seen` 行数 |
| M5-T2 | 详情和女优墙一键关注 | P1 | M5 | todo | | `app/static/app.js` 女优按钮和女优 browse 标题；`POST /api/subscriptions`；`app/routers/follow.py` 对已有 target 返回已有订阅 | 点一次后追更页出现该女优，规则是只提醒，名字与页面一致。再点显示已关注，条数不加。不打开自动下载。系列和片商详情没有这个按钮 | D2。`kind=actress`，名字和 target 用页面上已有的 |
| M5-T3 | 追更里的欧美新作打开详情 | P1 | M5 | todo | | `app/static/app.js` `renderFollow`；欧美详情请求 | 欧美未读新作能打开详情（标题、演员、磁链区）。JAV 番号仍进番号详情。「知道了」仍标记已读。scene 和 movie 都找不到时有说明，列表还在 | D3。`western_*` 订阅先按 scene 打开，404 再试 movie。M4 已完成时走同一 hash |
| M5-T4 | 没磁链的状态写明还会再试 | P1 | M5 | todo | | `app/static/app.js` `HIT_STATUS.no_magnet` 和 hit 行说明 | 该行能读到「暂无符合规则的磁链」以及 14 天内会再试。入队后文案变为「已入队」。后端 `_retry_codes` 行为不变 | D4。14 天与 `app/follow.py` 的 `FOLLOW_RETRY_FOR` 保持一致 |

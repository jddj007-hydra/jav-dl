# 状态

- 更新时间：2026-10-01
- 当前里程碑：M2 磁链理由、回详情、换源删除
- 正在做：无
- 下一步：M2-T3。同里程碑 M2-T1、M2-T2 是 P1，先做 P0 的 M2-T3
- 阻塞：无
- 本阶段完成摘要：
  - M1-T1：JAV 的 fanart.jpg 保存封面原图字节，poster.jpg 从右侧裁成 2:3。竖图、已是 2:3，或解码失败时两份都是原图，归档不因此失败
  - M1-T2：归档时按 samples.full 写入 extrafanart/fanart-01.jpg 起，最多 20 张。单张失败只记日志，已有同名文件不重复下载，样图不进封面缓存
  - M1-T3：NFO 演员写入已有头像 URL 的 thumb。空 URL 不写标签，库索引仍只有名字，不下载头像文件
  - M1-T4：重新刮削覆盖该番号的 nfo、poster、fanart、extrafanart，视频保留。封面下载失败不改旧文件。被动补缺和启动扫描不改已有海报。pytest 234 passed
- 与 prd/task 的偏差：无

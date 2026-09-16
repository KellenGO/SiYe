# 跨平台收藏指标补全

收藏列表先流式返回，再在原浏览器会话内读取详情（小并发 + 节流，见下）。按内容 ID 合并更新，保留多个收藏夹的归属，不重复增加卡片。仅影响收藏路径。

## 指标来源

| 平台 | 详情来源 | 补全字段 |
| --- | --- | --- |
| B站 | 现有 `get_video_info`，读取 `View.stat` | view / like / reply / favorite / coin，另带 share |
| 小红书 | 现有 `get_note_by_id`，携带收藏条目的 xsec_token / xsec_source | interact_info.liked_count / comment_count / collected_count / share_count |
| 知乎 | `/api/v4/answers/{id}`、`articles/{id}`、`zvideos/{id}` 原始 JSON | voteup_count / comment_count / favlists_count；阅读字段仅取内容对象顶层，视频仅取 play_count |
| 抖音 | 原收藏列表 | 点赞、评论、收藏、分享；不额外请求尚未证实能补播放量的接口 |

知乎不经过旧的 ZhihuContent 模型，避免丢掉阅读量和收藏数。禁止把回答所属问题的浏览量填到回答上。不同内容类型和账号能获取的字段可能不同。

小红书缺少访问令牌时跳过详情；没有用其他内容的令牌替代。未返回、非法数值不填零；接口明确返回的零保留并显示。中文单位或带 `+` 的计数换算为整数，同时在 `metrics_approximate` 中标注近似字段。“10万+”不代表精确十万。

## 请求与缓存

- 每条内容至多发起一次详情调用，内容在多个收藏夹出现时去重。平台现有客户端的错误处理仍生效。
- 详情调用由**两个带节流的槽位**并发执行：每个槽位两次请求之间间隔 1.5 秒（整体约 1.3 次/秒），单次最多等待 12 秒，详情阶段总预算 150 秒。串行 + 2 秒间隔在 120 秒内只能补约 50 条，而 B站单个收藏夹最多 100 条 —— 那正是「一半完整、一半 failed」的来源。这些值不是平台保证的安全额度。
- 详情错误（限流等）立即结束该平台的补全，其余槽位一并停下；列表和已完成的详情结果保留，任务可以返回 partial。未完成条目标记 failed。
- **预算用尽不是错误**：不再抛超时异常、不再把平台判成「同步超时」，已经流出的行保留列表里就有的计数，剩下的留给下次同步（那时多半是缓存命中）。
- 成功响应中的计数缓存 6 小时，位置**跟着收藏库走**（`%LOCALAPPDATA%\SiYe\data\.cache\favorite_metrics\`），源码版与发行版共用，换版本不会重新打一遍详情请求。缓存仅含计数、近似字段和采集时间；文件名为平台、类型及内容 ID 的摘要。不写入令牌、Cookie、正文或收藏夹名称。
- 缓存命中仍保留原采集时间；列表本次已有的计数优先于旧详情缓存。缓存写入失败不阻止显示数据。未返回的字段也要等缓存过期才重新探测。
- 页面展示详情读取进度、未完成数量及最早详情采集时间。complete 指已覆盖该平台当前补全目标；小红书的目标为赞评藏，不表示浏览量也已获取。

## 2026-09-12 单条真实收藏验证

| 平台 | 结果 |
| --- | --- |
| B站 | 播放 419452、点赞 4991、评论 178、收藏 13524、投币 2289 |
| 小红书 | 点赞约 100000、评论 7805、收藏约 33000；另有分享约 18000 |
| 知乎 | 阅读 6504984、赞同 140937、评论 5479、收藏 218051 |
| 抖音 | 点赞 3418、评论 29、收藏 5368、分享 519；列表未返回播放量 |

每个平台只探测一条；这些样本不代表所有收藏或所有知乎内容类型。没有宣称能获取小红书浏览量或抖音他人作品播放量。

可复验命令：`.venv/Scripts/python.exe scripts/favorite_metrics_probe.py xhs bilibili zhihu douyin`。使用现有登录会话，每平台只读取一条，输出计数和状态，不打印令牌或原始响应。

## 本次文件职责

- `aggregate_search/favorite_metrics.py`：字段提取、带节流的并发补全、缓存与计数质量标记。
- `media_platform/{bilibili,xhs,zhihu}/core.py`：收藏列表完成后执行详情阶段。
- `aggregate_search/{worker,models}.py`：流式发送更新及补全元数据。
- `api/services/favorites_job_manager.py`：替换同一内容的更新、保留部分结果、匹配超时预算。
- `webui/src/types/search.ts`、`webui/src/components/favorites/FavoritesPage.tsx`：补全状态类型与收藏页说明、进度。
- `webui/src/lib/resultTools.ts`：显示已知零值。
- `tests/test_favorite_metrics.py`、`tests/test_remote_favorites.py`、`webui/tests/metricOrder.test.ts`：补全、缓存、失败保留与零值回归验证。
- `scripts/favorite_metrics_probe.py`：有界真实收藏验证。

# 观看历史

## 一句话

用户点开看过的搜索结果会自动记进本机「历史」，方便回头再找——不需要任何手动操作。

## 代码入口

| 职责 | 位置 |
|---|---|
| 后端存储层（views 表） | `api/services/watch_history_store.py` |
| 后端路由 | `api/routers/history.py` |
| 后端请求模型 | `api/schemas/history.py` |
| 后端注册入口 | `api/main.py` |
| 前端 API 客户端 | `webui/src/lib/historyApi.ts` |
| 前端状态 hook | `webui/src/hooks/useHistory.ts` |
| 前端历史页 | `webui/src/components/history/HistoryPage.tsx` |
| 记录触发点（点击结果链接） | `webui/src/components/search/ResultTabs.tsx`、`webui/src/components/favorites/LocalContentDrawer.tsx` |
| 站内可展示正文取得／纯视频播放后记录 | `webui/src/components/reading/ReadingBody.tsx` |
| 导航与路由 | `webui/src/components/layout/Header.tsx`、`webui/src/App.tsx` |

## 关键决定

- 历史内容沿用[搜索体验的共同视觉约定](search-experience.md)：列表与网格使用一致的封面、类型、平台和指标表达。列表保留摘要、多指标与行内展开，切换回来恢复展开状态；不改变历史顺序，也不新增收藏操作。

- 视频有可靠时长时，在封面右下角显示分秒或时分秒；接入 B 站、抖音、小红书视频及知乎独立视频在已有响应中提供的时长，不为此追加请求。小红书已识别为视频的内容可读取视频流中以毫秒计的时长；缺失时不显示、不推算，也不回填旧收藏。图文配乐和回答、文章内嵌视频的时长不作为整条内容时长。时长随历史、收藏和备份快照保留，旧数据兼容。

- 封面底部使用带阴影的白色类型和指标，不铺整条黑色渐变。网格封面左下角、内容类型标签右侧，以列表同款图标加紧凑数字（k / M / B，如 15.4k、1.2M）展示一个已有主要指标，近似值用 ≈ 标识，悬停和无障碍名称保留完整含义：优先播放／阅读，其次点赞及其他已有互动数据，保留近似值标记；缺失不补零，真实零正常显示。聚合卡不叠加各来源数值，完整数据仍在详情查看。

- 内容视图开关使用简短的「列表 / 网格」。网格卡片的平台名称配主题色圆点，桌面上与原文及适用操作排在同一行；手机窄屏将操作统一排在平台下方。封面和标题继续打开详情，触屏保留较大的点击区域。

- **历史可切换列表与封面网格，独立记忆偏好**：直接展示历史保存的内容快照，不依赖本地收藏记录。列表保留标题链接与行内展开，两种视图都能在详情查看完整标题、简介和已有指标；原文与单条删除在卡片和详情均可操作；切换不改变当前筛选、页签、选择或已显示条数。保留最近浏览倒序、平台/关键词筛选、导出及分批显示，不新增收藏或备注操作。

- **独立存储，不复用收藏表**：新建 `views` 表，与收藏库 `items` 同库（`%LOCALAPPDATA%/SiYe/data/library.db`）但完全分开。原因见 `docs/decisions/2026-09-18-观看历史独立存储.md`——收藏页「全部」视图包含库里每一条内容，写进收藏等于自动收藏、历史会污染收藏。
- 字段照 `items` 的内容列（platform / content_id / content_type / title / snippet / author / url / published_at / cover_url / metrics JSON），去掉 note / in_default / watch_later，加 first_viewed_at / last_viewed_at / view_count。
- 同一内容（platform, content_id）只存一份：再次观看只更新 last_viewed_at 与 view_count，不重复插入。
- 只保留最近 1000 条，写入时按 last_viewed_at 滚动淘汰最旧的。
- 记录是**静默**的：前端 fire-and-forget（见 `recordView`），失败被吞掉，绝不阻塞或延迟跳转；数据只在本机，不上传。
- 历史页复用 `ResultTabs` 渲染卡片，新增 `disableSort`（严格按最近浏览倒序）与 `onDeleteItem`（单条删除）；最多 1000 条记录每批只渲染 100 条，不做「暂停记录」开关。

## 已知坑 / 边界

- 普通内容打开信息详情不记录观看；点击原文才记录。支持[站内阅读](content-reading.md)的四平台内容在取得可展示正文后、纯视频在实际开始播放后，按当前来源记录一次；刷新不重复，正文加载失败、空的受限正文与关闭未完成请求不记录。图片的后续 CDN 加载失败不会撤销记录。聚合结果不会因打开其它来源而自动将所有来源记为看过。
- 历史页的删除/清空走 react-query 刷新；删除单条只在「历史」页出现，搜索结果与收藏页不展示删除入口。
- 后端 `record_view` 对 platform/content_id/title/url 有白名单校验（url 必须是 http/https），非法内容直接拒绝，不会写库。

## 测试怎么跑

- 后端：`tests/test_watch_history_store.py`，跑法见 `AGENTS.md`（pytest + 临时 basetemp）。
- 前端：`webui/tests/historyApi.test.ts`（纯函数转换），跑法见 `AGENTS.md`（`node node_modules/typescript/bin/tsc -p tsconfig.test.json` 后 `node run-compiled-tests.mjs`）。

- 浏览器：`scripts/scroll_to_top_smoke.py` 使用模拟响应验证双视图、独立偏好、未收藏详情、原文记录、删除/清空与窄屏回顶。

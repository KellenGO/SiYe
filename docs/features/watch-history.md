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
| 导航与路由 | `webui/src/components/layout/Header.tsx`、`webui/src/App.tsx` |

## 关键决定

- **历史可切换列表与封面网格，独立记忆偏好**：直接展示历史保存的内容快照，不依赖本地收藏记录。列表保留标题链接与行内展开，两种视图都能在详情查看完整标题、简介和已有指标；原文与单条删除在卡片和详情均可操作；切换不改变当前筛选、页签、选择或已显示条数。保留最近浏览倒序、平台/关键词筛选、导出及分批显示，不新增收藏或备注操作。

- **独立存储，不复用收藏表**：新建 `views` 表，与收藏库 `items` 同库（`%LOCALAPPDATA%/SiYe/data/library.db`）但完全分开。原因见 `docs/decisions/2026-09-18-观看历史独立存储.md`——收藏页「全部」视图包含库里每一条内容，写进收藏等于自动收藏、历史会污染收藏。
- 字段照 `items` 的内容列（platform / content_id / content_type / title / snippet / author / url / published_at / cover_url / metrics JSON），去掉 note / in_default / watch_later，加 first_viewed_at / last_viewed_at / view_count。
- 同一内容（platform, content_id）只存一份：再次观看只更新 last_viewed_at 与 view_count，不重复插入。
- 只保留最近 1000 条，写入时按 last_viewed_at 滚动淘汰最旧的。
- 记录是**静默**的：前端 fire-and-forget（见 `recordView`），失败被吞掉，绝不阻塞或延迟跳转；数据只在本机，不上传。
- 历史页复用 `ResultTabs` 渲染卡片，新增 `disableSort`（严格按最近浏览倒序）与 `onDeleteItem`（单条删除）；最多 1000 条记录每批只渲染 100 条，不做「暂停记录」开关。

## 已知坑 / 边界

- 打开详情不记录观看；点击卡片原文或详情中的原平台链接才记录。聚合结果详情按实际打开的来源记录。
- 历史页的删除/清空走 react-query 刷新；删除单条只在「历史」页出现，搜索结果与收藏页不展示删除入口。
- 后端 `record_view` 对 platform/content_id/title/url 有白名单校验（url 必须是 http/https），非法内容直接拒绝，不会写库。

## 测试怎么跑

- 后端：`tests/test_watch_history_store.py`，跑法见 `AGENTS.md`（pytest + 临时 basetemp）。
- 前端：`webui/tests/historyApi.test.ts`（纯函数转换），跑法见 `AGENTS.md`（`node node_modules/typescript/bin/tsc -p tsconfig.test.json` 后 `node run-compiled-tests.mjs`）。

- 浏览器：`scripts/scroll_to_top_smoke.py` 使用模拟响应验证双视图、独立偏好、未收藏详情、原文记录、删除/清空与窄屏回顶。

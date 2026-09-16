# 搜索体验（结果列表 / 排序 / 去重 / 渐进展示）

## 一句话

一次搜索会把四个平台的结果合并成**一页**：先流式展示已经回来的平台，边搜边显示进度；
再按用户选的排序方式重排、跨平台去重、按平台分组，并把活动指标（点赞/播放等）补全后更新卡片。

## 代码入口

| 职责 | 位置 |
|---|---|
| 搜索状态机（事件 → 状态）、排序、去重、渐进展示 selector | `webui/src/lib/searchExperience.ts`（**1,339 行，全仓最大的前端模块，待拆分**） |
| 每平台搜索数量（1–40，默认 20，localStorage 持久化） | `webui/src/lib/platformLimits.ts` |
| 平台列表与 slug 守卫的**唯一来源** | `webui/src/lib/platformMeta.ts` |
| 搜索冷却 / 新鲜度 / 状态文案 | `webui/src/lib/statusDisplay.ts`、`webui/src/lib/searchPopover.ts` |
| 结果状态 → UI 文案（含"正在搜索，暂时显示上次结果"） | `webui/src/lib/searchExperience.ts` 的 `summarizeSearchError` / `selectSearchPresentation` |
| React 接线层 | `webui/src/hooks/useSearchExperience.ts`、`useAggregateSearch.ts` |
| 页面与卡片 | `webui/src/components/search/{SearchPage,SearchBar,ResultTabs,ResultCard,ResultTools,SearchPopover,PlatformStatus}.tsx` |
| 结果缓存（90s，按平台+关键词+数量+账号代数） | `api/services/result_cache.py` |
| 平台级冷却 | `api/services/search_metrics.py` 的 `PlatformCooldowns` |

## 关键决定

- **平台顺序只有一个来源**（`webui/src/lib/platformMeta.ts` 的 `PLATFORM_SLUGS`，顺序即 UI 顺序
  `小红书 → 抖音 → B站 → 知乎`）。历史上这个数组在 8 个文件里各写了一遍，
  加一个平台要改 8 处 —— 2026-09-14 收敛成一份。
- **每平台数量上限 40、默认 20**，写在 `platformLimits.ts`，只存 localStorage
  （key `aggregate_search_platform_limits_v1`），坏值逐字段回退，不阻止搜索。
- **搜索结果缓存 TTL 默认 90 秒**，可用 `MC_RESULTS_CACHE_TTL_SECONDS` 覆盖并夹紧到 `[60,120]`，
  设为 0 关闭；容量 200 条 LRU。缓存 key 里带**账号代数**，所以账号同步/失效后旧结果自动作废。
- **平台限流后进冷却**：基准 60 秒，重复触发翻倍，封顶 300 秒（`PlatformCooldowns`）；
  只影响该平台，其他平台照常搜。
- **跨平台去重的阈值是刻意保守的**：标题最短 6 字（完全相同才放宽到 4 字）、
  模糊相似度阈值 0.9、摘要最短 20 字，另外会剥掉"（附完整文档）"这类标题后缀。
  宁可漏合并也不误合并 —— 合并错了用户看不出原因。
- **排序只作用于已返回的结果**，不会为了排序去额外请求平台。
- 搜索历史只留最近 10 条（`MAX_HISTORY_ITEMS`），存 localStorage。
- 点击搜索框下拉层里的历史记录、推荐词或首页「最近搜索」时，只把关键词填入搜索框并聚焦；不会自动发起搜索，也不会用历史平台覆盖当前勾选。历史平台只以「上次」标签提供上下文，实际搜索范围始终由用户当前勾选决定。

## 已知坑 / 边界

- `webui/src/lib/searchExperience.ts` 同时承担状态机、排序、去重、历史、文案五件事，1,339 行。
  它的 **39 个导出里只有 11 个被生产代码用到**，其余纯粹是"为了测试而导出" ——
  把内部评分函数（如 `engagementScore`）提升成了公共 API，改算法就得同时改测试。
  拆分方案见 `docs/plans/2026-09-14-优化与精简方案.md` §4.4（尚未执行）。
- i18n 覆盖情况（2026-09-15 更新）：`search.*` 命名空间的键**已在搜索页、搜索框、搜索浮层接线**
  （`webui/src/components/search/SearchPage.tsx`、`SearchBar.tsx`、`SearchPopover.tsx`），切英文能生效。
  **仍未接线的是那些"还没有键"的文案**：首页两个面板的说明文字、探索轮次的全部文案、
  `formatTime` 的相对时间（刚刚 / N 分钟前 —— 它是纯函数，接线得先把 `t` 传进去）、
  以及错误摘要的安全文案。这些保持硬编码，需要时按批新增键。
  接线原则是**中文界面文案逐字不变**（值本就与组件一致的直接接；只有「前往设置」
  与既有键不同才新增键）。**唯一一处刻意的例外**：搜索框非首页态 placeholder
  从「搜索话题、人物或产品」改用了 locale 里更完整的既有文案
  「搜索一个话题、人物或产品…」—— 与其反过来把翻译改差，不如用这一版。
- 拼错 i18n 键时 i18next **不报错**，而是把键名当文案渲染出去（用户看到 `search.xxx`）。
  `tests/test_webui_ui_contract.py` 的 `test_i18n_keys_used_in_source_exist_in_every_locale`
  专治这个（扫源码里的 `t("ns.key")` 回查 locale）；同文件的
  `test_locales_define_the_same_keys` 保证两种语言的键集合一致。
- 结果卡片的活动指标来自各平台不同字段，缺失时显示为空而不是 0；知乎不返回收藏数、
  B 站投币只在详情里 —— 这些缺口是平台侧的，不做额外请求补齐。
- 并发上限：搜索时每个平台一个 worker 子进程，账号操作与搜索互斥（见
  `docs/features/extension-login.md` 的排他租约）。

## 测试怎么跑

前端（本机 npm 被拦，用 node 绝对路径）：

```shell
node node_modules/typescript/bin/tsc -p tsconfig.test.json
node run-compiled-tests.mjs
```

相关用例：`webui/tests/searchExperience.test.ts`、`progressiveDisplay.test.ts`、
`platformLimits.test.ts`、`searchPopover.test.ts`、`statusDisplay.test.ts`、
`metricOrder.test.ts`、`searchDedupContract.test.ts`、`searchCooldown.test.ts`；
后端缓存与冷却：`tests/test_result_cache.py`、`tests/test_expiring_local_cache.py`、
`tests/test_search_statistics.py`。

构建前端后运行 `scripts/result_library_smoke.py --screenshots`，用临时 SQLite 与模拟搜索响应检查分来源收藏、详情展开、筛选、导出、复制及备注持久化。`scripts/getting_started_smoke.py` 另会检查推荐词和历史记录只填词、保留当前平台，以及教程的平台勾选说明。脚本都走模拟接口，不会访问真实平台。

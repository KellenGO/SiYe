# 热搜卡片（各平台热搜词）

## 一句话

首页的「最近搜索」旁边的卡片换成**各平台热搜**：按平台并列展示该平台的热词，点一条直接用当前勾选的平台发起聚合搜索 —— 用来回答"同一条热搜，四个平台分别是什么反应"。

## 代码入口

| 职责 | 位置 |
|---|---|
| 卡片组件 | `webui/src/components/trending/TrendingBoard.tsx` |
| 前端 hook（拉取 + 手动刷新） | `webui/src/hooks/useTrending.ts` |
| 前端 API 与纯函数（规整 / 选平台 / 取前 N / 热度格式化） | `webui/src/lib/trendingApi.ts` |
| 后端路由 | `api/routers/trending.py` |
| 后端取数与缓存 | `api/services/trending.py` |
| 响应模型 | `api/schemas/trending.py` |
| 首页挂载点与点击行为 | `webui/src/components/search/SearchPage.tsx` 的 `handleTrendingPick` |

## 关键决定

- **只取"词"，不取内容**。榜单产物只有「平台 + 词 + 热度」，内容交给已有的聚合搜索。
  这样不必为四个平台各写一套榜单详情解析，也不碰登录态与浏览器。取舍见
  `docs/decisions/2026-09-19-热搜卡片取代最近搜到.md`。
- **数据层按平台返回，聚合只在展示层**。聚合不可逆：丢掉平台归属就答不出"这个词是谁在热"。
  将来若要多平台共现视图，从前端合并即可。
- **抖音走公开榜单接口**：不需要登录、不需要签名、不需要浏览器，纯 HTTP 一次请求
  （实测记录在 `docs/plans/2026-09-19-热搜榜方案.md`）。其它平台尚未接入，返回 `unavailable`。
- **独立缓存 5 分钟**（`MC_TRENDING_CACHE_TTL_SECONDS` 可调，夹紧在 [60, 3600]），
  **与搜索结果缓存无关，也不进搜索冷却** —— 看榜不该把搜索拖进冷却。
- **点击 = 直接搜**（有勾选平台时）：这是卡片的用法本身。一个平台都没勾选时只填入关键词，
  交给搜索框既有的"先勾选至少一个平台"提示，不绕过那条守卫。
- **取代了首页原来的「最近搜到」板块**：那块内容在搜索结果与历史里都能看到，
  而"各平台在热什么"是这里唯一能提供的新信息。首页偏好里的 `recent` 换成了 `trending`。

## 已知坑 / 边界

- **只有抖音可用**。小红书要登录 + `X-S` 签名、B站部分接口要 wbi 签名、知乎待验证，
  都还没接入；卡片会自动只显示有词的平台，不占位、不报错。
- 抖音榜是**分钟级**变化的，5 分钟缓存是"够用且省请求"的折中；用户可手动刷新（跳过后端缓存读取）。
- 上游挂了只给安全文案（"热搜暂时取不到，稍后再试"），**不回显上游响应原文**；
  单个平台失败不影响其它平台。
- 不写库、不上传：纯进程内缓存，进程退出即失效。
- 卡片依赖 `libs/douyin.js` 之外的纯 HTTP 路径，所以**不需要浏览器**；换平台接入时
  如果那条路需要签名，要重新评估（参见方案文档的候选接口表）。

## 测试怎么跑

- 后端：`.venv/Scripts/python.exe -m pytest -q tests/test_trending.py --basetemp=.tmp_pytest_xxx`
  （全部用假 fetcher，不访问网络）。
- 前端：`node node_modules/typescript/bin/tsc -p tsconfig.test.json` +
  `node run-compiled-tests.mjs`（`webui/tests/trending.test.ts` 覆盖纯函数）。
- 真机：起后端后打开首页，看卡片是否出现、点一条是否发起搜索、刷新是否更新。

# 返回首页（首页 / 结果页两个视图）

## 一句话

搜完一轮之后，用户可以从结果页回到首页（大字标 + 大搜索框 + 最近搜索 + 热搜卡片）看看别的，
**结果不会被清掉**，首页给一个「查看上次搜索」入口切回去。两个视图可以来回切。

## 代码入口

| 职责 | 位置 |
|---|---|
| 视图状态（`"results"` / `"home"`）+ `goHome` / `showLastResults` | `webui/src/components/search/SearchPage.tsx` |
| 结果页左上角的「← 返回首页」按钮（`.btn.primary.home-back`） | 同上，`<SearchBar>` 之前 |
| 首页的「查看上次搜索（N 条）」入口（`.home-back-row`） | 同上 |
| 按钮与视图的样式（绝对定位挂左侧、≤1340px 退化到搜索框上方） | `webui/src/index.css`（`.search-zone` 一组） |
| 文案 | `webui/src/i18n/locales/{zh-CN,en-US}/common.json` 的 `search.backToHome` / `search.viewLastSearch` |

## 关键决定

- **「返回首页」只切视图，不动数据**：不清结果、不清任务、不动平台勾选。
  清结果用的是另一个动作（搜索框上的「重置」），两者刻意分开。
- **按钮挂在搜索框左侧外面，不占它的宽度**：所以 `.search-zone` 用 `position: relative`
  + 按钮 `position: absolute; right: calc(100% + 12px)`。
  **不要改成 flex** —— flex 会把搜索框从 800px 挤窄（用户明确否掉了这一版）。
  窗口 ≤1340px 时左侧挂不下（结果页容器 1080px 居中，需要窗口 ≈1330px 才留得出按钮的位置），
  媒体查询里退化到搜索框上方，仍然不挤压。
- **首页不给 ⟳ 重搜**：首页那排平台只用于勾选搜索范围，单独重搜是结果页的动作
  （`onPlatformFetch={!isHome && displayJobResponse ? … : undefined}`）。
- **导航项「首页」只在被"新点一次"时切视图**（`homeRequested` 的 false→true 沿），
  挂载时不抢：带结果恢复页面（比如重启应用后恢复任务）仍然停在结果页 —— 这是原有行为，别改掉。
- 视图状态**只在内存**（刷新后按"有结果就是结果页"决定），不做持久化。

## 已知坑 / 边界

- `.search-zone` 包了一层之后，`.search-shell` 下原来那组 `>` 子选择器
  （`.search-shell > .search-area …`：800px 上限、输入框高度、`.scope` 对齐）必须跟着改成
  `.search-shell > .search-zone …`，否则结果页搜索框样式会整体失效。**改包裹层级要连子选择器一起改。**
- 首页的搜索框上边距（34px / 窄屏 29px）挂在 `.home > .search-zone` 上，
  不能留在 `.search-area` 上，否则首页会多出一份或丢失边距。
- 视图切换与"搜索中"的关系：发起点搜索（`busy`）时自动切到结果视图，
  否则用户会看着首页等结果。
- 前端没有 DOM 测试环境（`webui/tests` 全是纯逻辑测试），交互靠目测；
  结构约束由 `tests/test_webui_ui_contract.py` 盯着（按钮在 `<SearchBar>` 之前、
  用主题色类、`right: calc(100% + 12px)` 存在、`goHome` 不得调用 `handleResetLocal`）。

## 测试怎么跑

- `pytest -q tests/test_webui_ui_contract.py`（按钮位置 / 不清结果 / i18n 键）
- 前端通用跑法见 `AGENTS.md`（npm 被拦，用 node 绝对路径跑 tsc 与 `run-compiled-tests.mjs`）。
